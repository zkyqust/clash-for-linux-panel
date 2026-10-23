"""User-level defaults for new Bash/Zsh terminals. No core lifecycle operations."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import uuid

VARIABLES = ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY',
             'all_proxy', 'ALL_PROXY', 'no_proxy', 'NO_PROXY')
BEGIN = '# >>> clash-panel terminal defaults >>>'
END = '# <<< clash-panel terminal defaults <<<'


def environment(config):
    def port(value):
        return value if type(value) is int and 0 < value < 65536 else None
    mixed = port(config.get('mixed-port'))
    http = port(config.get('port')) or mixed
    socks = port(config.get('socks-port')) or mixed
    auth = (config.get('authentication') or [''])[0]
    prefix = ''
    if auth:
        user, _, password = str(auth).partition(':')
        prefix = urllib.parse.quote(user, safe='') + ':' + urllib.parse.quote(password, safe='') + '@'
    host = config.get('bind-address')
    host = '127.0.0.1' if host in (None, '', '*', '0.0.0.0', '::') else str(host)
    if ':' in host and not host.startswith('['):
        host = '[' + host + ']'
    values = {}
    if http:
        for key in VARIABLES[:4]:
            values[key] = f'http://{prefix}{host}:{http}'
    if socks:
        for key in VARIABLES[4:6]:
            values[key] = f'socks5h://{prefix}{host}:{socks}'
    values['no_proxy'] = values['NO_PROXY'] = 'localhost,127.0.0.1,::1'
    return values


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class TerminalProxySettings:
    def __init__(self, root, source, user_home=None):
        self.root = Path(root).resolve()
        self.source = source
        self.user_home = Path.home() if user_home is None else Path(user_home)
        self.state_path = self.root / 'data/panel-terminal-proxy.json'

    def state(self):
        if not self.state_path.exists():
            return {'enabled': False, 'installed': False}
        value = json.loads(self.state_path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
            raise ValueError('终端默认代理配置格式不正确。')
        return {'enabled': value['enabled'], 'installed': True}

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('终端默认代理开关必须是布尔值。')
        helper = self.root / 'data/terminal-proxy.py'
        shell = self.root / 'data/terminal-proxy.sh'
        python = shutil.which('python3') or sys.executable
        shell_text = ('# Managed by Clash Panel. Read the current saved settings at shell startup.\n'
                      'eval "$(' + shlex.quote(python) + ' ' + shlex.quote(str(helper)) +
                      ' ' + shlex.quote(str(self.root)) + ')"\n')
        block = (BEGIN + '\nif [ -r ' + shlex.quote(str(shell)) + ' ]; then\n    . ' +
                 shlex.quote(str(shell)) + '\nfi\n' + END)
        bash_login = next((self.user_home / name for name in ('.bash_profile', '.bash_login', '.profile')
                           if (self.user_home / name).exists()), self.user_home / '.profile')
        startup = list(dict.fromkeys([self.user_home / '.bashrc', bash_login,
                                     self.user_home / '.zshrc', self.user_home / '.zprofile']))
        changes = {helper: self.source.encode('utf-8'), shell: shell_text.encode('utf-8')}
        for path in startup:
            target = path.resolve() if path.is_symlink() else path
            text = target.read_text(encoding='utf-8') if target.exists() else ''
            if (BEGIN in text) != (END in text):
                raise ValueError('终端启动文件中的 Clash Panel 标记不完整：' + path.name)
            if BEGIN in text:
                text = re.sub(re.escape(BEGIN) + r'.*?' + re.escape(END), lambda _: block, text, flags=re.S)
            else:
                text = text.rstrip('\n') + '\n\n' + block + '\n'
            changes[target] = text.encode('utf-8')
        # Commit the toggle last, after all managed startup files have been installed.
        changes[self.state_path] = (json.dumps({'version': 1, 'enabled': enabled}) + '\n').encode()
        originals = {path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.exists()
                     else (None, 0o600) for path in changes}
        backup = self.root / 'data/backups' / ('panel-terminal-' + uuid.uuid4().hex)
        backup.mkdir(parents=True, mode=0o700)
        manifest = []
        for index, (path, (content, mode)) in enumerate(originals.items()):
            name = str(index) + '-' + path.name
            manifest.append({'path': str(path), 'backup': name if content is not None else None})
            if content is not None:
                atomic_write(backup / name, content)
        atomic_write(backup / 'manifest.json', json.dumps(manifest, ensure_ascii=False).encode('utf-8'))
        try:
            for path, content in changes.items():
                atomic_write(path, content, originals[path][1])
        except Exception:
            failures = []
            for path, (content, mode) in originals.items():
                try:
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write(path, content, mode)
                except OSError:
                    failures.append(path.name)
            detail = '恢复失败：' + '、'.join(failures) if failures else '已恢复原配置'
            raise ValueError('终端默认代理保存失败，' + detail + '；备份：' + str(backup)) from None
        return {'message': '终端默认代理已' + ('开启' if enabled else '关闭') +
                '并保存，之后新开的 Bash / Zsh 终端会使用此设置。内核状态未变更。'}


def shell_commands(root):
    # Only fixed variable names and shell-quoted values can reach eval in the managed hook.
    clear = 'unset ' + ' '.join(VARIABLES)
    try:
        root = Path(root)
        state = json.loads((root / 'data/panel-terminal-proxy.json').read_text(encoding='utf-8'))
        if type(state.get('enabled')) is not bool:
            raise ValueError('invalid proxy state')
        if not state['enabled']:
            return clear
        result = subprocess.run([str(root / 'bin/yq'), '-o=json', '.', str(root / 'data/runtime.yaml')],
                                capture_output=True, text=True, timeout=5, check=True)
        values = environment(json.loads(result.stdout) or {})
        return clear + '\n' + '\n'.join('export ' + key + '=' + shlex.quote(value)
                                         for key, value in values.items())
    except Exception:
        print('Clash Panel：无法读取终端默认代理配置，本终端未设置代理。', file=sys.stderr)
        return clear


if __name__ == '__main__':
    print(shell_commands(sys.argv[1]))

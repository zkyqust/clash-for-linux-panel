"""Saved, user-scoped core startup for interactive Bash/Zsh terminals."""
from contextlib import contextmanager
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
import uuid

BEGIN = '# >>> clash-panel core autostart >>>'
END = '# <<< clash-panel core autostart <<<'
LEGACY = ('# >>> clashctl terminal autostart >>>', '# <<< clashctl terminal autostart <<<')


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.core-autostart-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def startup_lock(root):
    # Share the bridge's configuration lock: a terminal cannot race a UI stop.
    with (Path(root) / 'data/.panel-config.lock').open('a+b') as handle:
        if os.name == 'posix':
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield


class CoreAutostartSettings:
    def __init__(self, root, source, user_home=None):
        self.root = Path(root).resolve()
        self.source = source
        self.user_home = Path.home() if user_home is None else Path(user_home)
        self.state_path = self.root / 'data/panel-core-autostart.json'
        self.helper = self.root / 'data/core-autostart.py'

    def startup_files(self):
        return [self.user_home / '.bashrc', self.user_home / '.zshrc']

    def state(self):
        if self.state_path.exists():
            value = json.loads(self.state_path.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
                raise ValueError('内核终端自启动配置格式不正确。')
            return {'enabled': value['enabled'], 'installed': True}
        legacy = any(p.is_file() and LEGACY[0] in p.read_text(encoding='utf-8') for p in self.startup_files())
        return {'enabled': legacy, 'installed': legacy}

    def capture(self):
        paths = [p.resolve() if p.is_symlink() else p for p in self.startup_files()]
        paths += [self.helper, self.state_path]
        return {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) if p.exists() else (None, 0o600)
                for p in dict.fromkeys(paths)}

    def restore(self, originals):
        failures = []
        for path, (content, mode) in originals.items():
            try:
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, content, mode)
            except OSError:
                failures.append(path.name)
        if failures:
            raise ValueError('内核自启动配置恢复失败：' + '、'.join(failures))

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('内核终端自启动开关必须是布尔值。')
        python = shutil.which('python3') or sys.executable
        invoke = ' '.join(shlex.quote(str(v)) for v in (python, self.helper, self.root))
        block = (BEGIN + '\ncase $- in\n    *i*)\n        if [ -f ' + shlex.quote(str(self.helper)) +
                 ' ]; then\n            ' + invoke + '\n        fi\n        ;;\nesac\n' + END)
        originals = self.capture()
        changes = {}
        for path in self.startup_files():
            target = path.resolve() if path.is_symlink() else path
            text = target.read_text(encoding='utf-8') if target.exists() else ''
            for first, last in [(BEGIN, END), LEGACY]:
                if text.count(first) != text.count(last) or text.count(first) > 1:
                    raise ValueError('终端启动文件中的内核自启动标记不完整：' + path.name)
                text = re.sub(re.escape(first) + r'.*?' + re.escape(last) + r'\n?', '', text, flags=re.S)
            if enabled:
                text = text.rstrip('\n') + '\n\n' + block + '\n'
            if enabled or originals[target][0] is not None:
                changes[target] = text.encode('utf-8')
        changes[self.helper] = self.source.encode('utf-8')
        changes[self.state_path] = (json.dumps({'version': 1, 'enabled': enabled}) + '\n').encode()
        backup = self.root / 'data/backups' / ('panel-core-autostart-' + uuid.uuid4().hex)
        backup.mkdir(parents=True, mode=0o700)
        manifest = []
        for index, (path, (content, mode)) in enumerate(originals.items()):
            name = str(index) + '-' + path.name
            manifest.append({'path': str(path), 'backup': name if content is not None else None})
            if content is not None:
                atomic_write(backup / name, content)
        atomic_write(backup / 'manifest.json', json.dumps(manifest, ensure_ascii=False).encode())
        try:
            for path, content in changes.items():
                atomic_write(path, content, originals[path][1])
        except Exception:
            self.restore(originals)
            raise ValueError('内核终端自启动保存失败，已恢复原配置；备份：' + str(backup)) from None


def run_autostart(root):
    root = Path(root).resolve()
    state = root / 'data/panel-core-autostart.json'
    if not state.is_file():
        return 0
    with startup_lock(root):
        value = json.loads(state.read_text(encoding='utf-8'))
        if type(value.get('enabled')) is not bool:
            raise ValueError('invalid core startup state')
        if not value['enabled']:
            return 0
        env = dict(os.environ, CLASHCTL_HOME=str(root))
        env.pop('BASH_ENV', None)
        env.pop('ENV', None)
        # A fixed noninteractive Bash command bypasses the terminal hook itself.
        command = 'source "$CLASHCTL_HOME/scripts/cmd/clashctl.sh" || exit; clashctl start'
        with (root / 'data/terminal-autostart.log').open('ab') as log:
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', command], env=env,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, timeout=45)
        if result.returncode:
            raise ValueError('core startup failed')
    return 0


if __name__ == '__main__':
    try:
        run_autostart(sys.argv[1])
    except Exception:
        print('Clash Panel：内核自动启动失败，请查看安装目录中的 data/terminal-autostart.log。', file=sys.stderr)

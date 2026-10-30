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


CORE_SERVICE_SCRIPT = '''source "$CLASHCTL_HOME/scripts/cmd/clashctl.sh" || exit

detect_service_manager || exit 1
if [ "$service_manager" = nohup ]; then
    _service_process_lock_acquire "$service_pid_path" || exit 1
    panel_rc=0
    if ! _service_process_record_pid "$service_pid_path" >/dev/null 2>&1; then
        if panel_identity=$("$CLASH_PANEL_PYTHON" -c "$CLASH_PANEL_CORE_SOURCE" --find-core "$BIN_KERNEL" "$CLASH_RESOURCES_DIR" "$CLASH_CONFIG_RUNTIME"); then
            if [ -n "$panel_identity" ]; then
                IFS=$'\t' read -r panel_pid panel_start panel_argv panel_exe <<<"$panel_identity"
                if _service_process_identity_matches "$panel_pid" "$panel_start" "$panel_argv" "$panel_exe"; then
                    _service_process_record_create "$service_pid_path" "$panel_pid" "$BIN_KERNEL" "$BIN_KERNEL" -d "$CLASH_RESOURCES_DIR" -f "$CLASH_CONFIG_RUNTIME" || panel_rc=1
                else
                    panel_rc=1
                fi
            fi
        else
            panel_rc=1
        fi
    fi
    _service_process_lock_release "$_SERVICE_PROCESS_LOCK_PATH" || exit 1
    [ "$panel_rc" -eq 0 ] || exit 1
fi
clashctl "$@"
'''


def find_core_process(binary, resources, runtime, proc_root='/proc'):
    """Recover one current-user process, using the same exact identity as upstream."""
    expected = b'\0'.join(os.fsencode(v) for v in (binary, '-d', resources, '-f', runtime)) + b'\0'
    executable = str(Path(binary).resolve())
    matches = []
    for process in Path(proc_root).iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.geteuid():
                continue
            before = (process / 'stat').read_text(encoding='utf-8', errors='replace').rsplit(') ', 1)[1].split()
            if before[0] in ('Z', 'X') or (process / 'cmdline').read_bytes() != expected:
                continue
            actual = os.readlink(process / 'exe')
            if actual.endswith(' (deleted)'):
                actual = actual[:-10]
            if actual != executable:
                continue
            identity = (process / 'exe').stat()
            after = (process / 'stat').read_text(encoding='utf-8', errors='replace').rsplit(') ', 1)[1].split()
            if before[19] != after[19] or after[0] in ('Z', 'X'):
                continue
            matches.append('\t'.join((process.name, before[19], expected.hex(),
                                      str(identity.st_dev) + ':' + str(identity.st_ino))))
        except (OSError, IndexError):
            # Processes may exit while /proc is being read; never guess their identity.
            continue
    if len(matches) > 1:
        raise ValueError('发现多个属于当前安装的内核进程，无法安全修正进程记录。')
    return matches[0] if matches else ''


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
        # SSH login shells do not necessarily source .bashrc on a fresh account.
        bash_login = next((self.user_home / name for name in ('.bash_profile', '.bash_login', '.profile')
                           if (self.user_home / name).exists()), self.user_home / '.profile')
        return [self.user_home / '.bashrc', bash_login, self.user_home / '.zshrc', self.user_home / '.zprofile']

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
        env['CLASH_PANEL_PYTHON'] = sys.executable
        env['CLASH_PANEL_CORE_SOURCE'] = Path(__file__).read_text(encoding='utf-8')
        command = CORE_SERVICE_SCRIPT
        with (root / 'data/terminal-autostart.log').open('ab') as log:
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', command, 'clash-panel', 'start'], env=env,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, timeout=45)
        if result.returncode:
            raise ValueError('core startup failed')
    return 0


if __name__ == '__main__':
    if sys.argv[1:2] == ['--find-core']:
        try:
            print(find_core_process(*sys.argv[2:5]))
        except Exception as exc:
            print('Clash Panel：无法核对内核进程：' + str(exc), file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    try:
        run_autostart(sys.argv[1])
    except Exception:
        print('Clash Panel：内核自动启动失败，请查看安装目录中的 data/terminal-autostart.log。', file=sys.stderr)

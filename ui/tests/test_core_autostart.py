import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).parents[1] / 'bridge/core_autostart.py'
spec = importlib.util.spec_from_file_location('core_autostart', SOURCE)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class CoreAutostartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panel core spaces ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'custom install'
        self.home = Path(self.temp.name) / 'user'
        (self.root / 'data').mkdir(parents=True)
        self.home.mkdir()
        self.rc = self.home / '.bashrc'
        self.rc.write_text('export LANG=C.UTF-8\n# keep user settings\n')
        self.settings = core.CoreAutostartSettings(self.root, SOURCE.read_text(encoding='utf-8'), self.home)

    def test_enable_is_idempotent_disable_removes_only_owned_hooks(self):
        proxy_block = '# >>> clash-panel terminal defaults >>>\ntrue\n# <<< clash-panel terminal defaults <<<\n'
        self.rc.write_text(self.rc.read_text() + proxy_block)
        self.settings.set_enabled(True)
        self.settings.set_enabled(True)
        self.assertEqual(self.rc.read_text().count(core.BEGIN), 1)
        self.assertIn(str(self.root), self.rc.read_text())
        self.assertTrue(self.settings.state()['enabled'])
        self.settings.set_enabled(False)
        self.assertNotIn(core.BEGIN, self.rc.read_text())
        self.assertIn(proxy_block, self.rc.read_text())
        self.assertIn('export LANG=C.UTF-8', self.rc.read_text())
        self.assertFalse(self.settings.state()['enabled'])

    def test_legacy_manual_hook_is_detected_and_removed_on_disable(self):
        self.rc.write_text(self.rc.read_text() + core.LEGACY[0] + '\nclashctl start\n' + core.LEGACY[1] + '\n')
        self.assertTrue(self.settings.state()['enabled'])
        self.settings.set_enabled(False)
        self.assertNotIn('clashctl start', self.rc.read_text())
        self.assertFalse(self.settings.state()['enabled'])

    def test_malformed_marker_does_not_write_any_files(self):
        self.rc.write_text(core.BEGIN)
        with self.assertRaises(ValueError): self.settings.set_enabled(True)
        self.assertFalse(self.settings.state_path.exists())
        self.assertFalse(self.settings.helper.exists())

    def test_disk_failure_rolls_back_hooks_and_state(self):
        original = core.atomic_write
        def fail_state(path, content, mode=0o600):
            if Path(path) == self.settings.state_path: raise OSError('disk full')
            return original(path, content, mode)
        before = self.rc.read_bytes()
        with patch.object(core, 'atomic_write', side_effect=fail_state):
            with self.assertRaisesRegex(ValueError, '已恢复'): self.settings.set_enabled(True)
        self.assertEqual(self.rc.read_bytes(), before)
        self.assertFalse(self.settings.helper.exists())
        self.assertFalse((self.home / '.zshrc').exists())

    def test_disabled_setting_never_calls_core(self):
        self.settings.set_enabled(False)
        with patch.object(core.subprocess, 'run') as run:
            core.run_autostart(self.root)
        run.assert_not_called()

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux Bash required')
    def test_interactive_shell_starts_core_but_disabled_and_background_shell_do_not(self):
        scripts = self.root / 'scripts/cmd'
        scripts.mkdir(parents=True)
        (scripts / 'clashctl.sh').write_text('detect_service_manager() { service_manager=fixture; }\nclashctl() { test "$1" = start || return 1; touch "$CLASHCTL_HOME/data/started"; }\n')
        self.settings.set_enabled(True)
        marker = self.root / 'data/started'
        env = dict(os.environ)
        env.pop('BASH_ENV', None)
        background = subprocess.run(['bash', '--noprofile', '--norc', '-c', '. "$1"', 'test', str(self.rc)], env=env, capture_output=True, timeout=10)
        self.assertEqual(background.returncode, 0)
        self.assertFalse(marker.exists())
        def terminal():
            return subprocess.run(['bash', '--noprofile', '--rcfile', str(self.rc), '-ic', 'printf ready'],
                                  env=env, capture_output=True, text=True, timeout=10)
        first = terminal()
        self.assertEqual(first.stdout, 'ready')
        self.assertTrue(marker.exists())
        self.settings.set_enabled(False)
        marker.unlink()
        self.assertEqual(terminal().stdout, 'ready')
        self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == 'posix', 'Linux file locks required')
    def test_waiting_terminal_rereads_disabled_state_after_ui_stop(self):
        self.settings.set_enabled(True)
        scripts = self.root / 'scripts/cmd'
        scripts.mkdir(parents=True)
        (scripts / 'clashctl.sh').write_text('detect_service_manager() { service_manager=fixture; }\nclashctl() { touch "$CLASHCTL_HOME/data/MUST_NOT_START"; }\n')
        process = None
        with core.startup_lock(self.root):
            code = '''import importlib.util,sys
from contextlib import contextmanager
spec=importlib.util.spec_from_file_location('core',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
original=m.startup_lock
@contextmanager
def announced(root):
    print('WAITING_FOR_LOCK',flush=True)
    with original(root): yield
m.startup_lock=announced
m.run_autostart(sys.argv[2])
'''
            process = subprocess.Popen([sys.executable, '-c', code, str(self.settings.helper), str(self.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(process.stdout.readline().strip(), b'WAITING_FOR_LOCK')
            self.settings.state_path.write_text(json.dumps({'version': 1, 'enabled': False}))
        out, err = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, err)
        self.assertFalse((self.root / 'data/MUST_NOT_START').exists())

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux Bash required')
    def test_existing_login_profile_without_bashrc_source_gets_its_own_hook(self):
        login = self.home / '.bash_profile'
        login.write_text('export LOGIN_ONLY=preserved\n')
        scripts = self.root / 'scripts/cmd'
        scripts.mkdir(parents=True)
        (scripts / 'clashctl.sh').write_text('detect_service_manager() { service_manager=fixture; }\nclashctl() { touch "$CLASHCTL_HOME/data/login-started"; }\n')
        self.settings.set_enabled(True)
        self.assertIn(core.BEGIN, login.read_text())
        self.assertFalse((self.home / '.profile').exists())
        p = subprocess.run(['bash', '--noprofile', '--norc', '-ic', '. "$1"; printf "%s" "$LOGIN_ONLY"',
                            'test', str(login)], capture_output=True, text=True, timeout=10)
        self.assertEqual(p.stdout, 'preserved')
        self.assertTrue((self.root / 'data/login-started').exists())
        self.settings.set_enabled(False)
        self.assertEqual(login.read_text().strip(), 'export LOGIN_ONLY=preserved')


class CoreProcessRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='core process spaces ')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.proc = self.folder / 'proc'
        self.proc.mkdir()
        self.uid = self.proc.stat().st_uid
        uid = patch.object(core.os, 'geteuid', return_value=self.uid, create=True)
        uid.start()
        self.addCleanup(uid.stop)
        self.binary = self.folder / 'mihomo'
        self.binary.write_bytes(b'fixture')
        self.resources = str(self.folder / 'resources')
        self.runtime = str(self.folder / 'runtime.yaml')
        self.argv = b'\0'.join(os.fsencode(x) for x in (str(self.binary), '-d', self.resources, '-f', self.runtime)) + b'\0'
        self.link = patch.object(core.os, 'readlink', return_value=str(self.binary))
        self.link.start()
        self.addCleanup(self.link.stop)

    def process(self, pid=123, argv=None, state='S'):
        p = self.proc / str(pid)
        p.mkdir()
        (p / 'cmdline').write_bytes(self.argv if argv is None else argv)
        (p / 'stat').write_text(str(pid) + ' (mihomo) ' + state + ' ' + ' '.join(['0'] * 18 + ['456']))
        (p / 'exe').write_bytes(b'fixture executable identity')
        return p

    def find(self):
        return core.find_core_process(str(self.binary), self.resources, self.runtime, self.proc)

    def test_exact_process_can_repair_a_missing_or_stale_pid_record(self):
        self.process()
        identity = self.find().split('\t')
        self.assertEqual(identity[:3], ['123', '456', self.argv.hex()])

    def test_other_installation_and_other_config_are_never_adopted(self):
        self.process(argv=self.argv.replace(b'runtime.yaml', b'other.yaml'))
        self.process(124, argv=self.argv.replace(b'mihomo', b'other'))
        self.assertEqual(self.find(), '')

    def test_other_user_and_zombie_are_never_adopted(self):
        self.process()
        with patch.object(core.os, 'geteuid', return_value=self.uid + 1):
            self.assertEqual(self.find(), '')
        (self.proc / '123/stat').write_text('123 (mihomo) Z ' + ' '.join(['0'] * 18 + ['456']))
        self.assertEqual(self.find(), '')

    def test_ambiguous_processes_fail_instead_of_guessing(self):
        self.process()
        self.process(124)
        with self.assertRaisesRegex(ValueError, '多个'):
            self.find()

    def test_changed_process_identity_is_not_adopted(self):
        p = self.process()
        original = Path.read_text
        calls = 0
        def read(path, *args, **kwargs):
            nonlocal calls
            value = original(path, *args, **kwargs)
            if path == p / 'stat':
                calls += 1
                if calls == 2:
                    value = value.replace('456', '789')
            return value
        with patch.object(Path, 'read_text', read):
            self.assertEqual(self.find(), '')

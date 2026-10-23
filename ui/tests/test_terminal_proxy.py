import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

source_path = Path(__file__).parents[1] / 'bridge/terminal_proxy.py'
spec = importlib.util.spec_from_file_location('terminal_proxy', source_path)
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


class TerminalDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panel proxy ')
        self.root = Path(self.temp.name) / 'clash'
        self.home = Path(self.temp.name) / 'user'
        (self.root / 'data').mkdir(parents=True)
        self.home.mkdir()
        (self.home / '.bashrc').write_text('export KEEP_ME=original\n')
        self.settings = proxy.TerminalProxySettings(self.root, source_path.read_text(encoding='utf-8'), self.home)

    def tearDown(self):
        self.temp.cleanup()

    def test_save_is_idempotent_and_preserves_existing_startup_contents(self):
        self.settings.set_enabled(True)
        self.settings.set_enabled(False)
        text = (self.home / '.bashrc').read_text()
        self.assertEqual(text.count(proxy.BEGIN), 1)
        self.assertIn('export KEEP_ME=original', text)
        self.assertEqual(self.settings.state(), {'enabled': False, 'installed': True})
        self.assertFalse((self.root / 'data/runtime.yaml').exists())

    def test_failed_commit_restores_all_original_files(self):
        original = proxy.atomic_write
        def fail_state(path, content, mode=0o600):
            if Path(path) == self.settings.state_path:
                raise OSError('disk full')
            return original(path, content, mode)
        with patch.object(proxy, 'atomic_write', side_effect=fail_state):
            with self.assertRaisesRegex(ValueError, '已恢复'):
                self.settings.set_enabled(True)
        self.assertEqual((self.home / '.bashrc').read_text(), 'export KEEP_ME=original\n')
        self.assertFalse(self.settings.state_path.exists())
        self.assertFalse((self.home / '.zshrc').exists())

    def test_malformed_hook_is_rejected_before_writes(self):
        (self.home / '.bashrc').write_text(proxy.BEGIN)
        with self.assertRaises(ValueError): self.settings.set_enabled(True)
        self.assertFalse(self.settings.state_path.exists())

    def test_commands_quote_authentication_and_follow_current_ports(self):
        self.settings.set_enabled(True)
        config = {'port': 0, 'mixed-port': 17890, 'socks-port': 0, 'authentication': ['user:p@ss:$(bad)']}
        result = type('R', (), {'stdout': json.dumps(config)})()
        with patch.object(proxy.subprocess, 'run', return_value=result):
            commands = proxy.shell_commands(self.root)
        self.assertIn('17890', commands)
        self.assertIn('p%40ss%3A%24%28bad%29', commands)
        self.assertNotIn('$(bad)', commands)
        self.settings.set_enabled(False)
        with patch.object(proxy.subprocess, 'run') as run:
            self.assertTrue(proxy.shell_commands(self.root).startswith('unset '))
        run.assert_not_called()

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'requires Linux Bash')
    def test_fresh_bash_uses_toggle_and_rereads_ports_without_core(self):
        (self.root / 'bin').mkdir()
        yq = self.root / 'bin/yq'
        yq.write_text('#!/usr/bin/env python3\nimport pathlib,sys\nprint(pathlib.Path(sys.argv[-1]).read_text())\n')
        yq.chmod(0o700)
        runtime = self.root / 'data/runtime.yaml'
        runtime.write_text(json.dumps({'port': 0, 'mixed-port': 17890, 'socks-port': 0}))
        env = dict(os.environ, HOME=str(self.home))
        for key in proxy.VARIABLES: env.pop(key, None)
        def read():
            p = subprocess.run(['bash', '--noprofile', '--rcfile', str(self.home / '.bashrc'), '-ic',
                                'printf "%s|%s|%s" "$http_proxy" "$ALL_PROXY" "$KEEP_ME"'],
                               capture_output=True, text=True, env=env, timeout=10)
            self.assertEqual(p.returncode, 0, p.stderr)
            return p.stdout
        self.settings.set_enabled(True)
        self.assertEqual(read(), 'http://127.0.0.1:17890|socks5h://127.0.0.1:17890|original')
        runtime.write_text(json.dumps({'port': 0, 'mixed-port': 17891, 'socks-port': 0}))
        self.assertIn(':17891', read())
        self.settings.set_enabled(False)
        self.assertEqual(read(), '||original')


if __name__ == '__main__': unittest.main()

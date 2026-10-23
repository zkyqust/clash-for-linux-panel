import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[2]
REQUIRED = ['.env.example', 'install.sh', 'uninstall.sh', 'scripts/preflight.sh',
            'scripts/lib/operation-lock.sh', 'scripts/lib/install-maintenance.sh',
            'scripts/cmd/clashctl.sh', 'scripts/cmd/update.sh']


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux Bash required')
class LocalSourceCopyTests(unittest.TestCase):
    def fixture(self, directory):
        source = Path(directory) / 'source'
        for relative in REQUIRED + ['.gitignore']:
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        for relative in ['.env', '.env.private', 'data/subscription.yaml', 'bin/mihomo',
                         'archives/kernel.gz', 'ui/build/extension.vsix', 'ui/__pycache__/bridge.pyc',
                         'ui/node_modules/module/index.js', 'ui/artifacts/result.json']:
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('private runtime data')
        return source

    def copy(self, source, target):
        target.mkdir()
        return subprocess.run(
            ['bash', '-c', 'source "$1"; _install_copy_local_source "$2" "$3" && _source_validate "$3"',
             'copy-test', str(ROOT / 'install.sh'), str(source), str(target)],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, 'CLASHCTL_INSTALL_RUNNING': '1'})

    def test_actual_source_copy_keeps_template_and_excludes_private_runtime_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.fixture(directory)
            target = Path(directory) / 'stage'
            result = self.copy(source, target)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((target / '.env.example').read_bytes(), (source / '.env.example').read_bytes())
            for relative in REQUIRED:
                self.assertTrue((target / relative).is_file(), relative)
            for relative in ['.env', '.env.private', 'data', 'bin', 'archives',
                             'ui/build', 'ui/__pycache__', 'ui/node_modules', 'ui/artifacts']:
                self.assertFalse((target / relative).exists(), relative)

    def test_required_template_cannot_be_a_symlink_to_private_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.fixture(directory)
            template = source / '.env.example'
            template.unlink()
            template.symlink_to(source / '.env.private')
            result = self.copy(source, Path(directory) / 'stage')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('.env.example', result.stderr)

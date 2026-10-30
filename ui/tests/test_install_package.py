import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

UI = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('install_package', UI / 'tools/install_package.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
META = json.loads((UI / 'package.json').read_text(encoding='utf-8'))


def package(metadata=META):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('extension/package.json', json.dumps(metadata))
        for name in ['extension.vsixmanifest', 'extension/src/extension.js',
                     'extension/bridge/bridge.py', 'extension/bridge/terminal_proxy.py',
                     'extension/bridge/core_autostart.py']:
            archive.writestr(name, 'fixture')
    return buffer.getvalue()


class ReleasedPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'ui'
        self.root.mkdir()
        (self.root / 'package.json').write_text(json.dumps(META))
        self.target = self.root / 'build' / ('clashctl-panel-%s.vsix' % META['version'])

    def test_supplied_package_needs_no_network_or_build(self):
        source = self.base / 'downloaded.vsix'
        source.write_bytes(package())
        with patch.object(installer.urllib.request, 'urlopen', side_effect=AssertionError('must stay local')):
            result = installer.prepare(self.root, source)
        self.assertEqual(result, self.target.resolve())
        self.assertEqual(result.read_bytes(), source.read_bytes())

    def test_existing_valid_package_is_reused(self):
        self.target.parent.mkdir()
        self.target.write_bytes(package())
        with patch.object(installer.urllib.request, 'urlopen', side_effect=AssertionError('must reuse cache')):
            self.assertEqual(installer.prepare(self.root), self.target.resolve())

    def test_supplied_wrong_version_is_rejected_before_copy(self):
        source = self.base / 'wrong.vsix'
        source.write_bytes(package({**META, 'version': '0.0.0'}))
        with self.assertRaisesRegex(ValueError, 'version'):
            installer.prepare(self.root, source)
        self.assertFalse(self.target.exists())

    def test_supplied_wrong_extension_is_rejected(self):
        source = self.base / 'wrong.vsix'
        source.write_bytes(package({**META, 'name': 'unrelated-extension'}))
        with self.assertRaisesRegex(ValueError, 'name'):
            installer.prepare(self.root, source)

    def test_missing_supplied_file_does_not_fall_back_to_download(self):
        with patch.object(installer.urllib.request, 'urlopen', side_effect=AssertionError('must not download')):
            with self.assertRaises(FileNotFoundError):
                installer.prepare(self.root, self.base / 'missing.vsix')

    def test_download_uses_matching_release_and_validates_result(self):
        with patch.object(installer.urllib.request, 'urlopen', return_value=io.BytesIO(package())) as opened:
            result = installer.prepare(self.root)
        self.assertEqual(opened.call_args.args[0],
                         'https://github.com/zkyqust/clash-for-linux-panel/releases/download/v%s/clashctl-panel-%s.vsix' % (META['version'], META['version']))
        installer.validate(result, META)

    def test_bad_download_preserves_existing_package_and_removes_partial_file(self):
        self.target.parent.mkdir()
        self.target.write_bytes(b'old invalid cache')
        with patch.object(installer.urllib.request, 'urlopen', return_value=io.BytesIO(b'not a VSIX')):
            with self.assertRaises(zipfile.BadZipFile):
                installer.prepare(self.root)
        self.assertEqual(self.target.read_bytes(), b'old invalid cache')
        self.assertEqual(list(self.target.parent.glob('.vsix-*')), [])

    def test_network_failure_leaves_no_partial_package(self):
        with patch.object(installer.urllib.request, 'urlopen', side_effect=urllib.error.URLError('network unavailable')):
            with self.assertRaises(urllib.error.URLError):
                installer.prepare(self.root)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob('.vsix-*')), [])


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux Bash required')
class SetupWorkflowTests(unittest.TestCase):
    def check_install(self, reuse=False, existing_geodata=False):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, user = base / 'source', base / 'user'
            (root / 'ui/tools').mkdir(parents=True)
            user.mkdir()
            shutil.copy2(UI.parent / 'setup.sh', root / 'setup.sh')
            shutil.copy2(UI / 'tools/install_package.py', root / 'ui/tools/install_package.py')
            (root / 'ui/package.json').write_text(json.dumps(META))
            (root / 'resources').mkdir()
            for name in ('Country.mmdb', 'geosite.dat'):
                (root / 'resources' / name).write_bytes(b'bundled geodata: ' + name.encode())
            # Source compilation must never run in a normal installation.
            (root / 'ui/tools/package.py').write_text('raise RuntimeError("source build was invoked")\n')
            (root / 'install.sh').write_text('''#!/usr/bin/env bash
set -eu
test "$1" = --local && test "$2" = --home
mkdir -p "$3/bin" "$3/scripts/cmd" "$3/data"
touch "$3/scripts/cmd/clashctl.sh"
printf '#!/bin/sh\\nexit 0\\n' > "$3/bin/mihomo"
cp "$3/bin/mihomo" "$3/bin/yq"
chmod 755 "$3/bin/mihomo" "$3/bin/yq"
printf '%s' "$3" > "$HOME/installer-target"
''')
            supplied = base / 'download.vsix'
            supplied.write_bytes(package())
            target = user / ('existing-directory' if reuse else 'clashctl')
            arguments = ['bash', str(root / 'setup.sh'), '--vsix', str(supplied)]
            if reuse:
                (target / 'scripts/cmd').mkdir(parents=True)
                (target / 'scripts/cmd/clashctl.sh').touch()
                (target / 'bin').mkdir()
                for name in ['mihomo', 'yq']:
                    (target / 'bin' / name).write_text('#!/bin/sh\nexit 0\n')
                    (target / 'bin' / name).chmod(0o755)
                (target / 'subscription-marker').write_text('keep existing configuration')
                if existing_geodata:
                    (target / 'resources').mkdir()
                    for name in ('Country.mmdb', 'geosite.dat'):
                        (target / 'resources' / name).write_bytes(b'keep updated geodata: ' + name.encode())
                arguments += ['--home', str(target)]
            env = os.environ.copy()
            env.pop('CLASHCTL_HOME', None)
            commands = base / 'commands'
            commands.mkdir()
            code = commands / 'code'
            code.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$HOME/extension-install-arguments"\n')
            code.chmod(0o755)
            env.update(HOME=str(user), PATH=str(commands) + ':/usr/bin:/bin:/usr/local/bin')
            result = subprocess.run(arguments, capture_output=True, text=True, env=env, timeout=25)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((target / 'ui/build' / self.vsix_name).read_bytes(), supplied.read_bytes())
            self.assertEqual((user / 'extension-install-arguments').read_text().splitlines(),
                             ['--install-extension', str(target / 'ui/build' / self.vsix_name), '--force'])
            for name in ('Country.mmdb', 'geosite.dat'):
                expected = (b'keep updated geodata: ' if existing_geodata else b'bundled geodata: ') + name.encode()
                self.assertEqual((target / 'resources' / name).read_bytes(), expected)
            if reuse:
                self.assertFalse((user / 'installer-target').exists())
                self.assertEqual((target / 'subscription-marker').read_text(), 'keep existing configuration')
                self.assertFalse((user / 'clashctl').exists())
            else:
                self.assertEqual((user / 'installer-target').read_text(), str(user / 'clashctl'))

    @property
    def vsix_name(self):
        return 'clashctl-panel-%s.vsix' % META['version']

    def test_new_install_uses_clashctl_directory_without_building(self):
        self.check_install()

    def test_existing_custom_install_is_reused_without_rename_or_rebuild(self):
        self.check_install(reuse=True)

    def test_existing_geodata_is_preserved_when_reusing_an_install(self):
        self.check_install(reuse=True, existing_geodata=True)

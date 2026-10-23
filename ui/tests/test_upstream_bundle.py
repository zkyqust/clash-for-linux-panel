import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class UpstreamBundleTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / 'UPSTREAM_FILES.json').read_text(encoding='utf-8'))

    def test_all_pinned_upstream_files_are_retained(self):
        metadata = json.loads((ROOT / 'UPSTREAM.json').read_text(encoding='utf-8'))
        self.assertEqual(self.manifest['commit'], metadata['commit'])
        self.assertEqual(len(self.manifest['files']), 83)
        for record in self.manifest['files']:
            with self.subTest(path=record['path']):
                path = ROOT / record['local_path']
                self.assertTrue(path.is_file(), str(path))
                data = path.read_bytes()
                if path.suffix not in ('.mmdb', '.dat'):
                    data = data.replace(b'\r\n', b'\n')
                self.assertEqual(hashlib.sha256(data).hexdigest(), record['sha256'])

    def test_geodata_is_bundled_at_the_core_resource_paths(self):
        records = {record['path']: record for record in self.manifest['files']}
        for name in ('Country.mmdb', 'geosite.dat'):
            path = 'resources/' + name
            self.assertEqual(records[path]['local_path'], path)
            self.assertEqual((ROOT / path).stat().st_size, records[path]['size'])


if __name__ == '__main__':
    unittest.main()

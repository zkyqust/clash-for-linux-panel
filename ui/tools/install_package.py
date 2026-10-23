#!/usr/bin/env python3
"""Use a published VSIX or a user-supplied package, without building source."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def validate(package, metadata):
    with zipfile.ZipFile(package) as archive:
        entry = archive.getinfo('extension/package.json')
        if entry.file_size > 65536:
            raise ValueError('VSIX package manifest is too large.')
        actual = json.loads(archive.read(entry))
        for key in ('name', 'publisher', 'version'):
            if actual.get(key) != metadata[key]:
                raise ValueError('VSIX %s does not match this source version (%s).' % (key, metadata[key]))
        for entry in ('extension.vsixmanifest', 'extension/src/extension.js', 'extension/bridge/bridge.py',
                      'extension/bridge/terminal_proxy.py'):
            archive.getinfo(entry)


def prepare(root=ROOT, supplied=None):
    root = Path(root)
    metadata = json.loads((root / 'package.json').read_text(encoding='utf-8'))
    name = '%s-%s.vsix' % (metadata['name'], metadata['version'])
    target = root / 'build' / name
    if supplied:
        source = Path(supplied).expanduser().resolve(strict=True)
        validate(source, metadata)
    else:
        if target.is_file():
            try:
                validate(target, metadata)
                return target.resolve()
            except (ValueError, KeyError, zipfile.BadZipFile):
                pass
        source = None
    target.parent.mkdir(parents=True, exist_ok=True)
    if source and source == target.resolve():
        return source
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.vsix-', delete=False) as handle:
            temporary = Path(handle.name)
            if source:
                with source.open('rb') as original:
                    shutil.copyfileobj(original, handle)
            else:
                url = 'https://github.com/zkyqust/clash-for-linux-panel/releases/download/v%s/%s' % (metadata['version'], name)
                print('Downloading published extension: ' + url, file=sys.stderr)
                with urllib.request.urlopen(url, timeout=30) as response:
                    size = 0
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > 50 * 1024 * 1024:
                            raise ValueError('VSIX download exceeds 50 MiB.')
                        handle.write(chunk)
        validate(temporary, metadata)
        temporary.replace(target)
        return target.resolve()
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', help='Use a downloaded VSIX instead of accessing GitHub.')
    args = parser.parse_args()
    try:
        print(prepare(supplied=args.file))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, 'Cannot prepare extension: %s\nDownload the matching VSIX on another computer, then run bash setup.sh --vsix /path/to/package.vsix.\n' % error)

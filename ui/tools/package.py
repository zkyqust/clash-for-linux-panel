#!/usr/bin/env python3
"""Build the dependency-free extension with Python 3; Node/npm are not required."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ['package.json', 'src/extension.js', 'src/backend.js', 'bridge/bridge.py',
         'bridge/terminal_proxy.py', 'bridge/core_autostart.py', 'media/app.js', 'media/style.css', 'media/controls.css',
         'media/clash.svg', 'media/preview.png', 'THIRD_PARTY_NOTICES.md']
RENAMED = {'README.md': 'readme.md', 'CHANGELOG.md': 'changelog.md', 'LICENSE': 'LICENSE.txt'}


def manifest(meta):
    ns = 'http://schemas.microsoft.com/developer/vsx-schema/2011'
    ET.register_namespace('', ns)
    def child(parent, name, attributes=None, text=None):
        item = ET.SubElement(parent, '{' + ns + '}' + name, attributes or {})
        item.text = text
        return item
    root = ET.Element('{' + ns + '}PackageManifest', {'Version': '2.0.0'})
    metadata = child(root, 'Metadata')
    child(metadata, 'Identity', {'Language': 'en-US', 'Id': meta['name'], 'Version': meta['version'], 'Publisher': meta['publisher']})
    child(metadata, 'DisplayName', text=meta['displayName'])
    child(metadata, 'Description', {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'}, meta['description'])
    child(metadata, 'Tags', text=','.join(meta.get('keywords', [])))
    child(metadata, 'Categories', text=','.join(meta.get('categories', ['Other'])))
    child(metadata, 'GalleryFlags', text='Public')
    properties = child(metadata, 'Properties')
    for key, value in {'Code.Engine': meta['engines']['vscode'], 'Code.ExtensionKind': 'workspace',
                       'Code.ExecutesCode': 'true', 'Code.ExtensionDependencies': '', 'Code.ExtensionPack': '',
                       'Services.GitHubFlavoredMarkdown': 'true', 'Services.Content.Pricing': 'Free'}.items():
        child(properties, 'Property', {'Id': 'Microsoft.VisualStudio.' + key, 'Value': value})
    child(metadata, 'License', text='extension/LICENSE.txt')
    child(child(root, 'Installation'), 'InstallationTarget', {'Id': 'Microsoft.VisualStudio.Code'})
    child(root, 'Dependencies')
    assets = child(root, 'Assets')
    for kind, path in [('Microsoft.VisualStudio.Code.Manifest', 'package.json'),
                       ('Microsoft.VisualStudio.Services.Content.Details', 'readme.md'),
                       ('Microsoft.VisualStudio.Services.Content.Changelog', 'changelog.md'),
                       ('Microsoft.VisualStudio.Services.Content.License', 'LICENSE.txt')]:
        child(assets, 'Asset', {'Type': kind, 'Path': 'extension/' + path, 'Addressable': 'true'})
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def build(output=None):
    meta = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))
    output = Path(output) if output else ROOT / 'build' / (meta['name'] + '-' + meta['version'] + '.vsix')
    output.parent.mkdir(parents=True, exist_ok=True)
    content_types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    for extension, mime in {'.json': 'application/json', '.js': 'application/javascript', '.css': 'text/css',
                            '.md': 'text/markdown', '.txt': 'text/plain', '.py': 'application/octet-stream',
                            '.png': 'image/png', '.svg': 'image/svg+xml', '.vsixmanifest': 'text/xml'}.items():
        content_types += '<Default Extension="' + extension + '" ContentType="' + mime + '"/>'
    content_types += '</Types>'
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content_types)
        archive.writestr('extension.vsixmanifest', manifest(meta))
        for relative in FILES:
            archive.write(ROOT / relative, 'extension/' + relative)
        for source, destination in RENAMED.items():
            archive.write(ROOT / source, 'extension/' + destination)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', help='Output VSIX path (default: ui/build/clashctl-panel-VERSION.vsix)')
    args = parser.parse_args()
    result = build(args.out)
    print(result)
    print('SHA256 ' + hashlib.sha256(result.read_bytes()).hexdigest())

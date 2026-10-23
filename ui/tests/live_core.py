"""Isolated Linux test: uses installed binaries, never the user's running core/config."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import uuid


def run(bridge):
    installed = Path.home() / 'clashctl'
    checks = []
    used = set()
    def free_port():
        while True:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                value = sock.getsockname()[1]
            if value not in used:
                used.add(value)
                return value
    with tempfile.TemporaryDirectory(prefix='clash-panel-test-') as directory:
        root = Path(directory)
        for folder in ['bin', 'scripts/cmd', 'data/profiles', 'resources']:
            (root / folder).mkdir(parents=True, exist_ok=True)
        for name in ['mihomo', 'yq']:
            (root / 'bin' / name).symlink_to(installed / 'bin' / name)
        (root / 'scripts/cmd/clashctl.sh').write_text('# isolated API fixture; no service commands\n')
        api_port, http_port, mixed_port, socks_port, new_http = [free_port() for _ in range(5)]
        mixin = {'external-controller': f'127.0.0.1:{api_port}', 'secret': uuid.uuid4().hex, 'bind-address': '127.0.0.1', 'allow-lan': False, 'port': http_port, 'mixed-port': mixed_port, 'socks-port': socks_port, 'tun': {'enable': False}}
        runtime = dict(mixin, mode='rule', proxies=[], **{'proxy-groups': [{'name': '测试分组', 'type': 'select', 'proxies': ['DIRECT', 'REJECT']}], 'rules': ['MATCH,DIRECT']})
        (root / 'data/mixin.yaml').write_text(json.dumps(mixin))
        (root / 'data/runtime.yaml').write_text(json.dumps(runtime))
        (root / 'data/config.yaml').write_text(json.dumps(runtime))
        (root / 'data/profiles.yaml').write_text(json.dumps({'use': '测试订阅', 'profiles': [{'name': '测试订阅', 'url': 'file:///test.yaml'}]}))
        process = None
        log = (root / 'core.log').open('ab')
        def start():
            proc = subprocess.Popen([str(root / 'bin/mihomo'), '-d', str(root / 'resources'), '-f', str(root / 'data/runtime.yaml')], stdout=log, stderr=log)
            for _ in range(50):
                if proc.poll() is not None:
                    raise RuntimeError('Isolated core failed: ' + (root / 'core.log').read_text()[-2500:])
                try:
                    bridge.Bridge(str(root)).request('/version', timeout=1)
                    return proc
                except Exception:
                    time.sleep(.15)
            proc.terminate(); proc.wait(timeout=5)
            raise RuntimeError('Isolated core API timed out')
        try:
            process = start()
            b = bridge.Bridge(str(root))
            b.dispatch('mode', {'mode': 'global'})
            assert b.yaml('data/mixin.yaml')['mode'] == 'global'
            checks.append('mode applied and persisted')
            b.dispatch('ports', {'ports': {'http': new_http, 'mixed': mixed_port, 'socks': socks_port}})
            assert b.request('/configs')['port'] == new_http
            assert b.yaml('data/runtime.yaml')['port'] == new_http
            checks.append('real listener changed and persisted')
            with socket.socket() as occupied:
                occupied.bind(('127.0.0.1', 0))
                try:
                    b.dispatch('ports', {'ports': {'http': occupied.getsockname()[1], 'mixed': mixed_port, 'socks': socks_port}})
                    raise AssertionError('occupied port was accepted')
                except ValueError as exc:
                    assert '占用' in str(exc)
            assert b.request('/configs')['port'] == new_http
            checks.append('occupied port rejected without changing core')
            b.dispatch('select', {'group': '测试分组', 'node': 'REJECT'})
            assert b.yaml('data/mixin.yaml')['profile']['store-selected'] is True
            assert b.request('/proxies')['proxies']['测试分组']['now'] == 'REJECT'
            checks.append('native selector storage enabled and selection applied')
            time.sleep(1)
            process.terminate(); process.wait(timeout=8)
            process = start()
            b = bridge.Bridge(str(root))
            config = b.request('/configs')
            assert config['mode'] == 'global'
            assert config['port'] == new_http
            assert b.request('/proxies')['proxies']['测试分组']['now'] == 'REJECT'
            assert b.yaml('data/profiles.yaml')['use'] == '测试订阅'
            checks.append('fresh core restored mode, ports, node and subscription metadata')
            if not Path('/dev/net/tun').exists():
                try:
                    b.dispatch('tun', {'enabled': True})
                    raise AssertionError('TUN enabled without device')
                except ValueError as exc:
                    assert '/dev/net/tun' in str(exc)
                assert process.poll() is None
                assert not b.request('/configs')['tun']['enable']
                checks.append('missing TUN device rejected before service changes')
            return {'ok': True, 'checks': checks, 'isolated': True, 'version': b.request('/version')['version']}
        finally:
            if process and process.poll() is None:
                process.terminate(); process.wait(timeout=8)
            log.close()


if __name__ == '__main__':
    spec = importlib.util.spec_from_file_location('bridge', Path(__file__).parents[1] / 'bridge/bridge.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print(json.dumps(run(module), ensure_ascii=False))

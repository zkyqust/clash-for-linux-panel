import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).parents[1] / 'bridge/bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class ProxyTests(unittest.TestCase):
    def test_port_zero_falls_back_to_mixed(self):
        env = bridge.proxy_environment({'port': 0, 'mixed-port': 7890, 'socks-port': 7891}, {'HTTP_PROXY': 'http://127.0.0.1:0', 'PATH': 'keep'})
        self.assertEqual(env['HTTP_PROXY'], 'http://127.0.0.1:7890')
        self.assertEqual(env['ALL_PROXY'], 'socks5h://127.0.0.1:7891')
        self.assertEqual(env['PATH'], 'keep')

    def test_dedicated_http_preferred(self):
        env = bridge.proxy_environment({'port': 7897, 'mixed-port': 7890}, {})
        self.assertTrue(env['HTTPS_PROXY'].endswith(':7897'))

    def test_invalid_ports_not_used(self):
        for value in [0, -1, 65536, None, '7890', True]:
            self.assertIsNone(bridge.port(value))
        env = bridge.proxy_environment({'port': 0}, {'http_proxy': 'http://stale:0', 'ALL_PROXY': 'socks5://stale:0'})
        self.assertNotIn('http_proxy', env)
        self.assertNotIn('ALL_PROXY', env)

    def test_authentication_encoding_and_ipv6(self):
        env = bridge.proxy_environment({'port': 7897, 'bind-address': '::1', 'authentication': ['user:p@ss:word']}, {})
        self.assertEqual(env['HTTP_PROXY'], 'http://user:p%40ss%3Aword@[::1]:7897')

    def test_name_validation(self):
        self.assertEqual(bridge.valid_name('台湾 $(touch /tmp/never) "节点"'), '台湾 $(touch /tmp/never) "节点"')
        for value in ['', '-all', 'bad\nname', None]:
            with self.assertRaises(ValueError): bridge.valid_name(value)

    def test_secret_redaction(self):
        redacted = bridge.redact('Bearer token123 https://user:pass@host.test/s/secret?x=secret secret: abc other-private', ['other-private'])
        for value in ['token123', 'user:pass', '/s/secret', '?x=', 'abc', 'other-private']:
            self.assertNotIn(value, redacted)


class FakeBridge(bridge.Bridge):
    def __init__(self, root):
        self.root = Path(root)
        self.config = {'port': 7897, 'mixed-port': 7890, 'socks-port': 7891, 'mode': 'rule', 'secret': 'never-show', 'external-controller': '127.0.0.1:9090', 'profile': {'store-selected': True}, 'tun': {'enable': False}}
        self.secrets = ['never-show']
        self.calls = []
        self.live = json.loads(json.dumps(self.config))
        self.running = True
        self.selected = '台湾'
        (self.root / 'data/profiles').mkdir(parents=True, exist_ok=True)
        for relative in ['data/mixin.yaml', 'data/runtime.yaml', 'data/config.yaml']:
            (self.root / relative).write_text(json.dumps(self.config))
        meta = {'use': '主订阅', 'profiles': [{'name': '主订阅', 'url': 'https://host.test/s/private?token=abc', 'path': str(self.root / 'data/profiles/a.yaml')}, {'name': '副订阅', 'url': 'file:///home/test/config.yaml', 'path': str(self.root / 'data/profiles/b.yaml')}]}
        (self.root / 'data/profiles.yaml').write_text(json.dumps(meta))
        for file in ['a.yaml', 'b.yaml']:
            (self.root / 'data/profiles' / file).write_text('private subscription')

    def yaml(self, relative):
        path = self.root / relative
        return json.loads(path.read_text()) if path.exists() else {}

    def core_startup_settings(self):
        settings = super().core_startup_settings()
        settings.user_home = self.root / 'core-user'
        settings.user_home.mkdir(exist_ok=True)
        return settings

    def stage_yaml(self, relative, updates):
        value = self.yaml(relative)
        for key, content in updates.items():
            cursor = value
            parts = key.split('.')
            for part in parts[:-1]: cursor = cursor.setdefault(part, {})
            cursor[parts[-1]] = content
        return json.dumps(value).encode()

    def verify_patch(self, patch):
        if any(self.live.get(k) != v for k, v in patch.items()): raise ValueError('not applied')

    def request(self, route, method='GET', body=None, timeout=5):
        self.calls.append((route, method, body))
        if route == '/version':
            if not self.running: raise ValueError('stopped')
            return {'version': '1.test'}
        if route == '/configs?force=true' and method == 'PUT': self.live = json.loads(body['payload']); return {}
        if route == '/configs':
            if method == 'PATCH': self.live.update(body)
            return dict(self.live)
        if route.startswith('/proxies/') and method == 'PUT': self.selected = body['name']; return {}
        if route == '/proxies': return {'proxies': {'国外': {'type': 'Selector', 'all': ['日本', '台湾'], 'now': self.selected}, '自动': {'type': 'URLTest', 'all': ['日本'], 'now': '日本'}, '台湾': {'type': 'Shadowsocks', 'history': []}, '日本': {'type': 'Shadowsocks', 'history': [{'delay': 200}]}}}
        if route == '/connections': return {'connections': [{'metadata': {'host': 'site.test', 'destinationPort': '443'}, 'chains': ['台湾', '国外']}], 'uploadTotal': 10}
        return {}

    def core_port_open(self):
        return self.running

    def cli(self, args, extra_env=None):
        self.calls.append(args)
        if args == ['stop']: self.running = False
        if args == ['start']: self.running = True
        if args == ['mixin', '-e']: self.running = True; self.live = self.yaml('data/runtime.yaml')
        if args[:1] == ['tun'] and len(args) == 2:
            enabled = args[1] == 'on'
            self.live['tun'] = {'enable': enabled}
            for rel in ['data/mixin.yaml', 'data/runtime.yaml']:
                (self.root / rel).write_bytes(self.stage_yaml(rel, {'tun.enable': enabled}))
        if args[:2] in (['sub', 'use'], ['sub', 'del']):
            meta = self.yaml('data/profiles.yaml')
            if args[1] == 'use': meta['use'] = args[2]
            else:
                record = next(p for p in meta['profiles'] if p['name'] == args[2])
                Path(record['path']).unlink()
                meta['profiles'] = [p for p in meta['profiles'] if p['name'] != args[2]]
            (self.root / 'data/profiles.yaml').write_text(json.dumps(meta))
        return {'message': 'ok'}


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.b = FakeBridge(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def test_core_switch_saves_and_removes_hooks_without_proxy_or_node_changes(self):
        settings = self.b.core_startup_settings()
        startup = settings.user_home / '.bashrc'
        startup.write_text('export LANG=C.UTF-8\n')
        previous = {p: p.read_bytes() for p in (self.b.root / 'data').glob('*.yaml')}
        proxy = self.b.root / 'data/panel-terminal-proxy.json'
        proxy.write_text('{"version": 1, "enabled": true}\n')
        self.b.dispatch('start', {})
        self.assertTrue(self.b.running)
        self.assertTrue(settings.state()['enabled'])
        self.assertIn('clash-panel core autostart', startup.read_text())
        self.b.dispatch('stop', {})
        self.assertFalse(self.b.running)
        self.assertFalse(settings.state()['enabled'])
        self.assertEqual(startup.read_text().strip(), 'export LANG=C.UTF-8')
        self.assertTrue(json.loads(proxy.read_text())['enabled'])
        for path, content in previous.items(): self.assertEqual(path.read_bytes(), content)

    def test_start_failure_restores_startup_files_and_stopped_state(self):
        self.b.running = False
        original = self.b.cli
        def fail_start(args, extra_env=None):
            if args == ['start']: raise ValueError('cannot start')
            return original(args, extra_env)
        with patch.object(self.b, 'cli', side_effect=fail_start):
            with self.assertRaisesRegex(ValueError, '已恢复'):
                self.b.dispatch('start', {})
        settings = self.b.core_startup_settings()
        self.assertFalse(settings.state()['enabled'])
        self.assertFalse((settings.user_home / '.bashrc').exists())
        self.assertFalse(self.b.running)

    def test_stop_failure_restores_enabled_setting_and_running_state(self):
        self.b.dispatch('start', {})
        original = self.b.cli
        def fail_stop(args, extra_env=None):
            result = original(args, extra_env)
            if args == ['stop']: raise ValueError('cannot stop')
            return result
        with patch.object(self.b, 'cli', side_effect=fail_stop):
            with self.assertRaisesRegex(ValueError, '已恢复'):
                self.b.dispatch('stop', {})
        self.assertTrue(self.b.running)
        self.assertTrue(self.b.core_startup_settings().state()['enabled'])

    def test_snapshot_excludes_private_data(self):
        snapshot = self.b.snapshot()
        encoded = json.dumps(snapshot)
        for secret in ['never-show', '/s/private', 'token=abc']:
            self.assertNotIn(secret, encoded)
        self.assertTrue(snapshot['running'])
        self.assertEqual(snapshot['profiles'][1]['source'], '本地 YAML')
        self.assertTrue(next(g for g in snapshot['groups'] if g['name'] == '国外')['selectable'])
        self.assertFalse(next(g for g in snapshot['groups'] if g['name'] == '自动')['selectable'])

    def test_terminal_default_toggle_leaves_core_and_node_configs_untouched(self):
        settings = self.b.terminal_settings()
        settings.user_home = self.b.root / 'fake-user'
        settings.user_home.mkdir()
        previous = {name: (self.b.root / name).read_bytes() for name in ['data/runtime.yaml', 'data/mixin.yaml', 'data/profiles.yaml']}
        with patch.object(self.b, 'terminal_settings', return_value=settings):
            self.b.dispatch('terminalProxy', {'enabled': True})
            self.b.dispatch('terminalProxy', {'enabled': False})
        self.assertEqual(self.b.calls, [])
        for name, content in previous.items():
            self.assertEqual((self.b.root / name).read_bytes(), content)

    def test_single_delay_failure_preserves_reason_without_selecting(self):
        original = self.b.request
        def request(route, method='GET', body=None, timeout=5):
            if '/delay?' in route:
                raise ValueError('test endpoint rejected the request')
            return original(route, method, body, timeout)
        with patch.object(self.b, 'request', side_effect=request):
            result = self.b.dispatch('delay', {'nodes': ['日本']})
        self.assertEqual(result['delays'], {'日本': 0})
        self.assertIn('rejected', result['errors']['日本'])
        self.assertEqual(self.b.selected, '台湾')

    def test_only_select_existing_node_in_selector(self):
        self.b.dispatch('select', {'group': '国外', 'node': '日本'})
        self.assertTrue(any(isinstance(c, tuple) and c[1:] == ('PUT', {'name': '日本'}) for c in self.b.calls))
        for group, node in [('国外', '不存在'), ('自动', '日本')]:
            with self.assertRaises(ValueError): self.b.dispatch('select', {'group': group, 'node': node})

    def test_active_profile_delete_refused(self):
        with self.assertRaises(ValueError): self.b.dispatch('subDelete', {'name': '主订阅'})
        self.assertFalse(any(isinstance(c, list) for c in self.b.calls))

    def test_sub_commands_keep_user_inputs_as_arguments(self):
        self.b.dispatch('subUpdate', {'name': '副订阅'})
        self.assertEqual(self.b.calls[-1], ['sub', 'update', '副订阅'])
        self.b.dispatch('subAdd', {'name': 'my " $() name', 'url': 'https://host.test/s/token?a=1&b=2'})
        self.assertEqual(self.b.calls[-1], ['sub', 'add', '--name', 'my " $() name', 'https://host.test/s/token?a=1&b=2'])

    def test_local_import_is_private_and_not_implicitly_activated(self):
        self.b.dispatch('subImport', {'name': '配置', 'content': 'cHJveGllczogW10K'})
        files = list((self.b.root / 'data/imports').glob('panel-*.yaml'))
        self.assertEqual(len(files), 1)
        self.assertIn('--raw', self.b.calls[-1])
        self.assertNotIn('--use', self.b.calls[-1])

    def test_invalid_actions_and_urls_rejected(self):
        with self.assertRaises(ValueError): self.b.dispatch('shell', {'command': 'anything'})
        with self.assertRaises(ValueError): self.b.dispatch('subAdd', {'name': 'test', 'url': 'file:///etc/passwd'})
        with self.assertRaises(ValueError): self.b.dispatch('mode', {'mode': 'tun'})

    def test_cli_uses_fixed_bash_script_and_valid_proxy(self):
        def run(args, **kwargs):
            self.assertEqual(args[:5], ['bash', '--noprofile', '--norc', '-c', 'source "$CLASHCTL_HOME/scripts/cmd/clashctl.sh" || exit; clashctl "$@"'])
            self.assertEqual(args[-1], '$(echo never)')
            self.assertEqual(kwargs['env']['HTTP_PROXY'], 'http://127.0.0.1:7897')
            self.assertNotIn('BASH_ENV', kwargs['env'])
            self.assertNotIn('ENV', kwargs['env'])
            return type('Result', (), {'returncode': 0, 'stdout': 'https://host.test/s/private', 'stderr': ''})()
        with patch.dict(os.environ, {'BASH_ENV': '/unwanted/rc', 'ENV': '/unwanted/rc'}):
            with patch.object(bridge.subprocess, 'run', side_effect=run):
                result = bridge.Bridge.cli(self.b, ['sub', 'use', '$(echo never)'])
        self.assertNotIn('/s/private', result['message'])

    def test_successful_stop_command_cannot_claim_a_live_core_was_stopped(self):
        result = type('Result', (), {'returncode': 0, 'stdout': 'mihomo stopped', 'stderr': ''})()
        verify = lambda enabled: bridge.Bridge.verify_core_service(self.b, enabled, timeout=0)
        with patch.object(bridge.subprocess, 'run', return_value=result), patch.object(self.b, 'verify_core_service', side_effect=verify):
            with self.assertRaisesRegex(ValueError, '未确认关闭'):
                bridge.Bridge.cli(self.b, ['stop'])

    def test_successful_start_command_requires_a_ready_core(self):
        self.b.running = False
        result = type('Result', (), {'returncode': 0, 'stdout': 'mihomo started', 'stderr': ''})()
        verify = lambda enabled: bridge.Bridge.verify_core_service(self.b, enabled, timeout=0)
        with patch.object(bridge.subprocess, 'run', return_value=result), patch.object(self.b, 'verify_core_service', side_effect=verify):
            with self.assertRaisesRegex(ValueError, '未就绪'):
                bridge.Bridge.cli(self.b, ['start'])

    @unittest.skipUnless(os.name == 'posix', 'requires real Linux Bash')
    def test_real_bash_preserves_shell_metacharacters(self):
        scripts = self.b.root / 'scripts/cmd'
        scripts.mkdir(parents=True)
        (scripts / 'clashctl.sh').write_text('clashctl() { printf "%s\\n" "$@"; }\n')
        marker = self.b.root / 'MUST_NOT_EXIST'
        name = '中文 $(touch ' + str(marker) + ') "quoted"; false'
        result = bridge.Bridge.cli(self.b, ['sub', 'use', name])
        self.assertIn(name, result['message'])
        self.assertFalse(marker.exists())


class PersistenceTests(unittest.TestCase):
    setUp = OperationTests.setUp
    tearDown = OperationTests.tearDown
    def test_modes_written_to_both_configs(self):
        result = self.b.dispatch('mode', {'mode': 'global'})
        self.assertIn('已保存', result['message'])
        self.assertEqual(self.b.live['mode'], 'global')
        for rel in ['data/mixin.yaml', 'data/runtime.yaml']:
            self.assertEqual(self.b.yaml(rel)['mode'], 'global')

    def test_ports_validate_and_persist_zero(self):
        result = self.b.dispatch('ports', {'ports': {'http': 0, 'mixed': 7890, 'socks': 7891}})
        self.assertIn('已保存', result['message'])
        self.assertEqual(self.b.yaml('data/mixin.yaml')['port'], 0)
        self.assertTrue(bridge.proxy_environment(self.b.yaml('data/runtime.yaml'), {})['HTTP_PROXY'].endswith(':7890'))
        for values in [{'http': 7890, 'mixed': 7890, 'socks': 7891}, {'http': True, 'mixed': 7890, 'socks': 7891}, {'http': -1, 'mixed': 1, 'socks': 2}]:
            with self.assertRaises(ValueError): bridge.validate_ports(values)

    def test_failed_disk_write_restores_files_and_live_mode(self):
        original = bridge.atomic_write
        failed = False
        def fail_once(path, content, mode=0o600):
            nonlocal failed
            if Path(path) == self.b.root / 'data/runtime.yaml' and not failed:
                failed = True
                raise OSError('disk full')
            return original(path, content, mode)
        with patch.object(bridge, 'atomic_write', side_effect=fail_once):
            with self.assertRaisesRegex(ValueError, '已恢复'):
                self.b.dispatch('mode', {'mode': 'global'})
        self.assertEqual(self.b.live['mode'], 'rule')
        self.assertEqual(self.b.yaml('data/mixin.yaml')['mode'], 'rule')

    def test_no_tun_device_rejects_without_mutation(self):
        with patch.object(bridge, 'tun_capability', return_value={'canEnable': False, 'reason': '没有 TUN 设备'}):
            with self.assertRaisesRegex(ValueError, '没有 TUN'):
                self.b.dispatch('tun', {'enabled': True})
        self.assertFalse(self.b.calls)

    def test_tun_on_and_off_are_persistent(self):
        with patch.object(bridge, 'tun_capability', return_value={'canEnable': True, 'reason': ''}):
            self.b.dispatch('tun', {'enabled': True})
            self.assertTrue(self.b.yaml('data/mixin.yaml')['tun']['enable'])
            self.b.dispatch('tun', {'enabled': False})
            self.assertFalse(self.b.yaml('data/runtime.yaml')['tun']['enable'])

    def test_enable_native_node_cache_once(self):
        self.b.config['profile'] = {}
        for rel in ['data/mixin.yaml', 'data/runtime.yaml']:
            (self.b.root / rel).write_bytes(self.b.stage_yaml(rel, {'profile.store-selected': False}))
        self.b.dispatch('select', {'group': '国外', 'node': '日本'})
        self.assertTrue(self.b.yaml('data/mixin.yaml')['profile']['store-selected'])
        self.assertEqual(self.b.selected, '日本')
        reloads = sum(isinstance(c, tuple) and c[0] == '/configs?force=true' for c in self.b.calls)
        self.b.dispatch('select', {'group': '国外', 'node': '台湾'})
        self.assertEqual(sum(isinstance(c, tuple) and c[0] == '/configs?force=true' for c in self.b.calls), reloads)
        self.assertEqual(self.b.saved_choices()['主订阅']['国外'], '台湾')

    def test_active_delete_switches_replacement_first(self):
        self.b.dispatch('subDelete', {'name': '主订阅', 'allowActive': True, 'replacement': '副订阅'})
        meta = self.b.yaml('data/profiles.yaml')
        self.assertEqual(meta['use'], '副订阅')
        self.assertEqual([p['name'] for p in meta['profiles']], ['副订阅'])
        self.assertLess(self.b.calls.index(['sub', 'use', '副订阅']), self.b.calls.index(['sub', 'del', '主订阅']))

    def test_subscription_choice_is_saved(self):
        result = self.b.dispatch('subUse', {'name': '副订阅'})
        self.assertEqual(self.b.yaml('data/profiles.yaml')['use'], '副订阅')
        self.assertIn('已启用并保存', result['message'])

    def test_delete_last_stops_core_and_clears_active_config(self):
        self.b.dispatch('start', {})
        self.b.dispatch('subDelete', {'name': '副订阅'})
        self.b.dispatch('subDelete', {'name': '主订阅', 'allowActive': True})
        self.assertFalse(self.b.running)
        self.assertFalse(self.b.core_startup_settings().state()['enabled'])
        self.assertEqual(self.b.yaml('data/profiles.yaml')['profiles'], [])
        self.assertEqual(self.b.yaml('data/profiles.yaml')['use'], '')
        self.assertNotIn('private subscription', (self.b.root / 'data/config.yaml').read_text())

    def test_last_delete_failure_restores_profile_and_running_state(self):
        self.b.dispatch('start', {})
        self.b.dispatch('subDelete', {'name': '副订阅'})
        original_cli = self.b.cli
        def fail_delete(args, extra_env=None):
            if args == ['sub', 'del', '主订阅']:
                (self.b.root / 'data/profiles/a.yaml').unlink()
                raise ValueError('failed after unlink')
            return original_cli(args, extra_env)
        with patch.object(self.b, 'cli', side_effect=fail_delete):
            with self.assertRaisesRegex(ValueError, '已恢复'):
                self.b.dispatch('subDelete', {'name': '主订阅', 'allowActive': True})
        self.assertTrue(self.b.running)
        self.assertTrue(self.b.core_startup_settings().state()['enabled'])
        self.assertEqual(self.b.yaml('data/profiles.yaml')['use'], '主订阅')
        self.assertTrue((self.b.root / 'data/profiles/a.yaml').exists())


if __name__ == '__main__': unittest.main()

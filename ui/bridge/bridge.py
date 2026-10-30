"""Clash Panel bridge. Runs on the target Linux host; no external Python packages."""
import base64
import concurrent.futures
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import socket
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def redact(value, secrets=()):
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), '[已隐藏]')
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    def url(match):
        try:
            parsed = urllib.parse.urlsplit(match.group())
            return parsed.scheme + '://' + (parsed.hostname or '') + (':' + str(parsed.port) if parsed.port else '') + '/[已隐藏]'
        except ValueError:
            return '[链接已隐藏]'
    text = re.sub(r'(?:https?|socks5h?)://[^\s<>\"\']+', url, text, flags=re.I)
    text = re.sub(r'(Bearer\s+)\S+', r'\1[已隐藏]', text, flags=re.I)
    return re.sub(r'((?:secret|password|token|authorization)\s*[:=]\s*)[^\s,}]+', r'\1[已隐藏]', text, flags=re.I)


def port(value):
    # bool is a subclass of int; strings, null and port 0 are not listening ports.
    return value if type(value) is int and 1 <= value <= 65535 else None


def proxy_environment(config, inherited=None):
    env = dict(os.environ if inherited is None else inherited)
    for key in list(env):
        if key.lower() in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
            del env[key]
    http_port = port(config.get('port')) or port(config.get('mixed-port'))
    socks_port = port(config.get('socks-port')) or port(config.get('mixed-port'))
    auth = (config.get('authentication') or [''])[0]
    userinfo = ''
    if auth:
        user, _, password = str(auth).partition(':')
        userinfo = urllib.parse.quote(user, safe='') + ':' + urllib.parse.quote(password, safe='') + '@'
    bind = config.get('bind-address')
    host = '127.0.0.1' if bind in (None, '', '*', '0.0.0.0', '::') else str(bind)
    if ':' in host and not host.startswith('['):
        host = '[' + host + ']'
    if http_port:
        env['http_proxy'] = env['https_proxy'] = f'http://{userinfo}{host}:{http_port}'
        env['HTTP_PROXY'] = env['HTTPS_PROXY'] = env['http_proxy']
    if socks_port:
        env['all_proxy'] = env['ALL_PROXY'] = f'socks5h://{userinfo}{host}:{socks_port}'
    env['no_proxy'] = env['NO_PROXY'] = 'localhost,127.0.0.1,::1'
    return env


def valid_name(name):
    if not isinstance(name, str) or not name.strip() or name.startswith('-') or len(name) > 200 or re.search(r'[\x00-\x1f\x7f]', name):
        raise ValueError('名称不能为空、以 - 开头或含控制字符，最长 200 个字符。')
    return name


def validate_ports(values):
    keys = {'http': 'port', 'mixed': 'mixed-port', 'socks': 'socks-port'}
    if not isinstance(values, dict) or set(values) != set(keys):
        raise ValueError('请提供 HTTP、混合、SOCKS 三个端口。')
    if any(type(v) is not int or not 0 <= v <= 65535 for v in values.values()):
        raise ValueError('端口必须为 0–65535 的整数；0 表示关闭该端口。')
    active = [v for v in values.values() if v]
    if len(active) != len(set(active)):
        raise ValueError('HTTP、混合和 SOCKS 使用独立监听端口，不能重复。')
    return {keys[k]: v for k, v in values.items()}


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.panel-', dir=path.parent)
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
def config_lock(root):
    with (Path(root) / 'data/.panel-config.lock').open('a+b') as handle:
        if os.name == 'posix':
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('另一个配置操作正在进行，请稍后重试。') from None
        yield


def tun_capability():
    if not Path('/dev/net/tun').exists():
        return {'canEnable': False, 'reason': '容器未提供 /dev/net/tun，需要管理员挂载 TUN 设备并授予网络管理权限。'}
    if not shutil.which('ip'):
        return {'canEnable': False, 'reason': '目标 Linux 缺少 ip 命令，需要先安装 iproute2。'}
    try:
        status = Path('/proc/self/status').read_text()
        bound = re.search(r'^CapBnd:\s*([0-9a-f]+)', status, re.M | re.I)
        if bound and not int(bound.group(1), 16) & (1 << 12):
            return {'canEnable': False, 'reason': '容器未授予网络管理权限，需要管理员提供 NET_ADMIN 权限。'}
    except OSError:
        pass
    if os.geteuid() != 0:
        try:
            if subprocess.run(['sudo', '-n', 'true'], capture_output=True, timeout=3).returncode:
                raise ValueError('sudo unavailable')
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {'canEnable': False, 'reason': 'TUN 需要 root 或无需输入密码的 sudo；可在服务器终端授权后重试。'}
    return {'canEnable': True, 'reason': ''}


class Bridge:
    def __init__(self, home):
        if not isinstance(home, str) or not (home.startswith('/') or home.startswith('~/')):
            raise ValueError('安装目录需要是 Linux 绝对路径或 ~/clash。')
        self.root = Path(home).expanduser().resolve()
        if not (self.root / 'scripts/cmd/clashctl.sh').is_file() or not (self.root / 'bin/yq').is_file():
            raise ValueError(f'未找到 clashctl：{self.root}。请设置正确的安装目录。')
        self.config = self.yaml('data/runtime.yaml')
        self.secrets = [self.config.get('secret', '')] + list(self.config.get('authentication') or [])
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        address = str(self.config.get('external-controller') or '')
        if address.startswith('0.0.0.0:') or address.startswith('*:'):
            address = '127.0.0.1:' + address.rsplit(':', 1)[1]
        if address.startswith('[::]:'):
            address = '[::1]:' + address.rsplit(':', 1)[1]
        if not re.fullmatch(r'(?:127\.0\.0\.1|localhost|\[::1\]):[0-9]+', address):
            raise ValueError('此版本只连接目标服务器的本机控制接口。external-controller 需监听 127.0.0.1、localhost 或 ::1。')
        self.api = 'http://' + address

    def yaml(self, relative):
        target = self.root / relative
        if not target.exists():
            return {}
        result = subprocess.run([str(self.root / 'bin/yq'), '-o=json', '.', str(target)], capture_output=True, text=True, timeout=8)
        if result.returncode:
            raise ValueError('无法读取配置文件：' + relative)
        return json.loads(result.stdout) or {}

    def request(self, route, method='GET', body=None, timeout=5):
        headers = {'Authorization': 'Bearer ' + str(self.config.get('secret') or '')}
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.api + route, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError('控制接口返回的数据过大。')
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise ValueError('控制接口认证失败，请确认 runtime.yaml 与运行中的内核一致。') from None
            raise ValueError(f'控制接口请求失败：HTTP {exc.code}。') from None

    def profiles(self):
        meta = self.yaml('data/profiles.yaml')
        profiles = []
        for item in meta.get('profiles') or []:
            url = str(item.get('url') or '')
            self.secrets.append(url)
            try:
                parsed = urllib.parse.urlsplit(url)
                source = '本地 YAML' if parsed.scheme == 'file' else parsed.hostname or '未知来源'
            except ValueError:
                source = '未知来源'
            profiles.append({'name': item.get('name', ''), 'source': source, 'updated': item.get('updated', ''), 'active': item.get('name') == meta.get('use')})
        return profiles

    def terminal_settings(self):
        source = globals().get('TERMINAL_PROXY_SOURCE')
        if source is None:
            source = Path(__file__).with_name('terminal_proxy.py').read_text(encoding='utf-8')
        namespace = {'__name__': 'clash_panel_terminal_proxy'}
        exec(compile(source, 'terminal_proxy.py', 'exec'), namespace)
        return namespace['TerminalProxySettings'](self.root, source)

    def core_startup_settings(self):
        source = globals().get('CORE_AUTOSTART_SOURCE')
        if source is None:
            source = Path(__file__).with_name('core_autostart.py').read_text(encoding='utf-8')
        namespace = {'__name__': 'clash_panel_core_autostart'}
        exec(compile(source, 'core_autostart.py', 'exec'), namespace)
        return namespace['CoreAutostartSettings'](self.root, source)

    def set_core_service(self, enabled):
        settings = self.core_startup_settings()
        with config_lock(self.root):
            originals = settings.capture()
            try:
                self.request('/version', timeout=1)
                was_running = True
            except (OSError, ValueError):
                was_running = False
            settings.set_enabled(enabled)
            try:
                self.cli(['start' if enabled else 'stop'])
            except Exception as exc:
                settings.restore(originals)
                try:
                    self.cli(['start' if was_running else 'stop'])
                except Exception:
                    raise ValueError('内核操作失败，自启动设置已恢复，请刷新检查内核运行状态。' + str(exc)) from None
                raise ValueError('内核操作失败，已恢复原设置和运行状态。' + str(exc)) from None
        return {'message': ('内核已开启，新开的 Bash / Zsh 交互终端会自动启动内核。' if enabled else
                            '内核已关闭，并已取消新终端自动启动。')}

    def saved_choices(self):
        path = self.root / 'data/panel-selections.json'
        if not path.exists():
            return {}
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('节点选择记录格式不正确，请检查 data/panel-selections.json。')
        return value

    def stage_yaml(self, relative, updates):
        # Callers pass only internal field paths and validated primitive values.
        expression = ' | '.join('.' + '.'.join(json.dumps(k) for k in key.split('.')) + ' = ' + json.dumps(value) for key, value in updates.items())
        result = subprocess.run([str(self.root / 'bin/yq'), expression, str(self.root / relative)], capture_output=True, timeout=8)
        if result.returncode:
            raise ValueError('配置无法更新：' + relative)
        return result.stdout

    def backup(self, relatives):
        originals = {}
        folder = self.root / 'data/backups' / ('panel-' + uuid.uuid4().hex)
        folder.mkdir(parents=True, mode=0o700)
        for relative in relatives:
            path = self.root / relative
            originals[relative] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            atomic_write(folder / relative.replace('/', '__'), originals[relative][0])
        return originals, folder

    def restore(self, originals):
        for relative, (content, mode) in originals.items():
            atomic_write(self.root / relative, content, mode)

    def proxy_host(self, config):
        bind = str(config.get('bind-address') or '127.0.0.1').strip('[]')
        return '127.0.0.1' if not config.get('allow-lan') or bind in ('*', '0.0.0.0', '::') else bind

    def check_ports(self, patch, current):
        current_ports = {current.get(k, 0) for k in ('port', 'mixed-port', 'socks-port')}
        host = self.proxy_host(current)
        for key, number in patch.items():
            if key not in ('port', 'mixed-port', 'socks-port') or not number or number in current_ports:
                continue
            kinds = [socket.SOCK_STREAM] if key == 'port' else [socket.SOCK_STREAM, socket.SOCK_DGRAM]
            for kind in kinds:
                try:
                    with socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, kind) as probe:
                        probe.bind((host, number))
                except OSError:
                    raise ValueError(f'端口 {number} 已被占用或当前进程无权使用，请换一个端口。') from None

    def verify_patch(self, patch):
        current = self.request('/configs')
        if any(current.get(k, 0) != v for k, v in patch.items()):
            raise ValueError('运行中的内核未应用所选配置。')
        for key, value in patch.items():
            if key in ('port', 'mixed-port', 'socks-port') and value:
                with socket.create_connection((self.proxy_host(current), value), timeout=2):
                    pass

    def apply_settings(self, patch, label):
        with config_lock(self.root):
            current = self.request('/configs')
            self.check_ports(patch, current)
            staged = {rel: self.stage_yaml(rel, patch) for rel in ('data/mixin.yaml', 'data/runtime.yaml')}
            originals, folder = self.backup(staged)
            old = {key: current.get(key, 0) for key in patch}
            changed = {key: 0 for key in patch if key in ('port', 'mixed-port', 'socks-port') and patch[key] != old[key]}
            try:
                # Release changed listeners first so HTTP/mixed/SOCKS ports may swap safely.
                if changed:
                    self.request('/configs', 'PATCH', changed)
                self.request('/configs', 'PATCH', patch)
                self.verify_patch(patch)
                for relative, content in staged.items():
                    atomic_write(self.root / relative, content, originals[relative][1])
            except Exception as exc:
                failures = []
                try:
                    if changed:
                        self.request('/configs', 'PATCH', changed)
                    self.request('/configs', 'PATCH', old)
                    self.verify_patch(old)
                except Exception as failure:
                    failures.append(str(failure))
                try:
                    self.restore(originals)
                except Exception as failure:
                    failures.append(str(failure))
                if failures:
                    raise ValueError(f'{label}失败，恢复未完成，请检查日志；原配置备份：{folder}。' + str(exc)) from None
                raise ValueError(f'{label}失败，已恢复原配置：' + str(exc)) from None
            return {'message': label + '已保存并生效，重启及更新订阅后会保留。'}

    def ensure_node_storage(self):
        if (self.yaml('data/mixin.yaml').get('profile') or {}).get('store-selected') is True and (self.config.get('profile') or {}).get('store-selected') is True:
            return
        with config_lock(self.root):
            live = self.request('/configs')
            previous = self.request('/proxies').get('proxies', {})
            choices = {name: item.get('now') for name, item in previous.items() if item.get('type') == 'Selector' and item.get('now') in item.get('all', [])}
            updates = {'profile.store-selected': True, 'mode': live.get('mode', 'rule'), 'tun.enable': bool((live.get('tun') or {}).get('enable'))}
            updates.update({key: live.get(key, 0) for key in ('port', 'mixed-port', 'socks-port')})
            staged = {rel: self.stage_yaml(rel, updates) for rel in ('data/mixin.yaml', 'data/runtime.yaml')}
            originals, folder = self.backup(staged)
            # Enable Mihomo's own persistent selector cache. Reload is needed only once.
            try:
                for relative, content in staged.items():
                    atomic_write(self.root / relative, content, originals[relative][1])
                self.request('/configs?force=true', 'PUT', {'payload': staged['data/runtime.yaml'].decode()}, timeout=20)
                self.request('/version')
                available = self.request('/proxies').get('proxies', {})
                for group, node in choices.items():
                    if node in available.get(group, {}).get('all', []):
                        self.request('/proxies/' + urllib.parse.quote(group, safe=''), 'PUT', {'name': node})
                self.config = self.yaml('data/runtime.yaml')
            except Exception as exc:
                self.restore(originals)
                try:
                    self.request('/configs?force=true', 'PUT', {'payload': originals['data/runtime.yaml'][0].decode()}, timeout=20)
                    for group, node in choices.items():
                        self.request('/proxies/' + urllib.parse.quote(group, safe=''), 'PUT', {'name': node})
                except Exception:
                    raise ValueError(f'节点保存设置失败，恢复内核未完成；原配置备份：{folder}。') from None
                raise ValueError('节点保存设置失败，已恢复配置：' + str(exc)) from None

    def set_tun(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('TUN 开关必须是布尔值。')
        if enabled:
            capability = tun_capability()
            if not capability['canEnable']:
                raise ValueError(capability['reason'])
        with config_lock(self.root):
            self.request('/version')
            originals, folder = self.backup(['data/mixin.yaml', 'data/runtime.yaml'])
            try:
                self.cli(['tun', 'on' if enabled else 'off'])
                applied = self.request('/configs')
                if not enabled and (applied.get('tun') or {}).get('enable'):
                    # Allow disabling a configured TUN even if no live interface exists.
                    self.request('/configs', 'PATCH', {'tun': {'enable': False}})
                    applied = self.request('/configs')
                if bool((applied.get('tun') or {}).get('enable')) != enabled:
                    raise ValueError('内核的 TUN 状态未变更。')
                if enabled:
                    self.cli(['tun'])  # Upstream checks an UP interface and its owner, not just YAML.
                # Upstream may return early when already off; persist the explicit choice in both files.
                for rel in originals:
                    if (self.yaml(rel).get('tun') or {}).get('enable') is not enabled:
                        atomic_write(self.root / rel, self.stage_yaml(rel, {'tun.enable': enabled}), originals[rel][1])
            except Exception as exc:
                self.restore(originals)
                try:
                    self.cli(['mixin', '-e'], {'EDITOR': shutil.which('true') or '/usr/bin/true'})
                except Exception:
                    raise ValueError(f'TUN 切换失败，恢复内核未完成；原配置备份：{folder}。' + str(exc)) from None
                raise ValueError('TUN 切换失败，已恢复原配置：' + str(exc)) from None
            return {'message': 'TUN 已' + ('开启' if enabled else '关闭') + '，选择已保存到全局配置。'}

    def delete_profile(self, params):
        name = valid_name(params.get('name'))
        profiles = self.profiles()
        profile = next((p for p in profiles if p['name'] == name), None)
        if not profile:
            raise ValueError('订阅不存在，请刷新。')
        if not profile['active']:
            return self.cli(['sub', 'del', name])
        if params.get('allowActive') is not True:
            raise ValueError('删除使用中的订阅前，需要确认接替订阅或停止内核。')
        replacement = params.get('replacement')
        if replacement:
            if not any(p['name'] == replacement and not p['active'] for p in profiles):
                raise ValueError('接替订阅不存在，请刷新。')
            self.cli(['sub', 'use', valid_name(replacement)])
            if self.yaml('data/profiles.yaml').get('use') != replacement:
                raise ValueError('接替订阅未生效，原订阅未删除。')
            self.cli(['sub', 'del', name])
            return {'message': f'订阅“{name}”已删除，当前使用“{replacement}”。'}
        if len(profiles) != 1:
            raise ValueError('还有其他订阅，请选择一个接替订阅。')
        with config_lock(self.root):
            meta = self.yaml('data/profiles.yaml')
            if meta.get('use') != name or len(meta.get('profiles') or []) != 1:
                raise ValueError('订阅状态已改变，请刷新后重试。')
            source = Path(meta['profiles'][0]['path']).resolve()
            try:
                relative_source = source.relative_to((self.root / 'data/profiles').resolve())
            except ValueError:
                raise ValueError('该订阅文件位于标准订阅目录以外，请在终端中处理。') from None
            profile_rel = 'data/profiles/' + relative_source.as_posix()
            originals, folder = self.backup(['data/profiles.yaml', 'data/config.yaml', 'data/runtime.yaml', profile_rel])
            startup = self.core_startup_settings()
            startup_originals = startup.capture()
            try:
                startup.set_enabled(False)
                self.cli(['stop'])
                atomic_write(self.root / 'data/profiles.yaml', self.stage_yaml('data/profiles.yaml', {'use': ''}), originals['data/profiles.yaml'][1])
                self.cli(['sub', 'del', name])
                atomic_write(self.root / 'data/config.yaml', b'# No active subscription. Add one in Clash Panel.\n', originals['data/config.yaml'][1])
                # Keep controller connectivity even on installations that put it only in runtime.
                keys = ('port', 'mixed-port', 'socks-port', 'bind-address', 'allow-lan', 'authentication', 'external-controller', 'secret', 'mode', 'profile', 'tun')
                empty_runtime = {key: self.config[key] for key in keys if key in self.config}
                empty_runtime.update(self.yaml('data/mixin.yaml'))
                atomic_write(self.root / 'data/runtime.yaml', json.dumps(empty_runtime).encode(), originals['data/runtime.yaml'][1])
            except Exception as exc:
                self.restore(originals)
                startup.restore(startup_originals)
                try:
                    self.cli(['mixin', '-e'], {'EDITOR': shutil.which('true') or '/usr/bin/true'})
                except Exception:
                    raise ValueError(f'删除失败，恢复内核未完成；原配置备份：{folder}。' + str(exc)) from None
                raise ValueError('删除失败，已恢复订阅配置：' + str(exc)) from None
            return {'message': '最后一个订阅已删除，服务器 Clash 已停止。添加并启用新订阅后可以继续使用。'}

    def snapshot(self):
        info = {'home': str(self.root), 'host': socket.gethostname(), 'profiles': self.profiles(), 'running': False,
                'ports': {'http': port(self.config.get('port')), 'mixed': port(self.config.get('mixed-port')), 'socks': port(self.config.get('socks-port'))},
                'tun': bool((self.config.get('tun') or {}).get('enable')), 'tunAvailable': Path('/dev/net/tun').exists(), 'tunCapability': tun_capability(),
                'nodePersistence': bool((self.config.get('profile') or {}).get('store-selected')),
                'groups': [], 'nodes': {}, 'mode': self.config.get('mode', 'rule'), 'warnings': []}
        try:
            info['terminalProxy'] = self.terminal_settings().state()
        except (OSError, ValueError):
            info['terminalProxy'] = {'enabled': False, 'installed': False, 'error': True}
            info['warnings'].append('无法读取终端默认代理配置，请重新保存开关设置。')
        try:
            info['coreAutostart'] = self.core_startup_settings().state()
        except (OSError, ValueError):
            info['coreAutostart'] = {'enabled': False, 'installed': False, 'error': True}
            info['warnings'].append('无法读取内核终端自启动设置，请重新设置内核服务开关。')
        if not port(self.config.get('port')) and port(self.config.get('mixed-port')):
            info['warnings'].append('独立 HTTP 端口未启用；插件使用混合端口创建代理终端，避开 :0。')
        try:
            version = self.request('/version')
            info['running'] = True
            info['version'] = version.get('version', '')
            runtime = self.request('/configs')
            info['mode'] = runtime.get('mode', info['mode'])
            info['ports'] = {'http': port(runtime.get('port')), 'mixed': port(runtime.get('mixed-port')), 'socks': port(runtime.get('socks-port'))}
            info['tun'] = bool((runtime.get('tun') or {}).get('enable', info['tun']))
            proxies = self.request('/proxies').get('proxies', {})
            for name, item in proxies.items():
                history = item.get('history') or []
                info['nodes'][name] = {'type': item.get('type', ''), 'delay': history[-1].get('delay') if history else None}
                if isinstance(item.get('all'), list):
                    info['groups'].append({'name': name, 'type': item.get('type', ''), 'now': item.get('now', ''), 'all': item['all'], 'selectable': item.get('type') == 'Selector'})
            info['groups'].sort(key=lambda g: (g['name'] == 'GLOBAL', g['name']))
            try:
                active_profile = self.yaml('data/profiles.yaml').get('use', '')
                for group, node in self.saved_choices().get(active_profile, {}).items():
                    if node not in proxies.get(group, {}).get('all', []):
                        info['warnings'].append(f'上次选择的节点“{node}”已不在策略组“{group}”中，请重新选择。')
            except (OSError, ValueError, AttributeError):
                info['warnings'].append('无法读取节点选择记录；内核仍使用自身的节点缓存。')
        except Exception as exc:
            info['apiError'] = redact(exc, self.secrets)
            info['warnings'].append('控制接口不可用：可能尚未启动、端口不可达或认证失败。')
        return info

    def core_port_open(self):
        address = urllib.parse.urlsplit(self.api)
        try:
            with socket.create_connection((address.hostname, address.port), timeout=0.5):
                return True
        except ConnectionRefusedError:
            return False

    def verify_core_service(self, enabled, timeout=3):
        deadline = time.monotonic() + timeout
        while True:
            try:
                if enabled:
                    self.request('/version', timeout=0.5)
                    return
                if not self.core_port_open():
                    return
            except (OSError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise ValueError('内核启动后控制接口未就绪。' if enabled else
                                 '内核停止命令已返回，但控制接口仍可访问或无法核验；未确认关闭。')
            time.sleep(0.1)

    def cli(self, args, extra_env=None):
        # Fixed Bash program; every user-controlled value is a separate positional argument.
        env = proxy_environment(self.config)
        # Container startup hooks can overwrite CLASHCTL_HOME before the fixed script runs.
        # Load only the explicitly selected installation, not noninteractive shell startup files.
        env.pop('BASH_ENV', None)
        env.pop('ENV', None)
        env['CLASHCTL_HOME'] = str(self.root)
        env.update(extra_env or {})
        if args[:2] in (['sub', 'add'], ['sub', 'update']):
            try:
                self.request('/version', timeout=1)
            except Exception:
                # After deleting the last profile the local proxy is intentionally stopped.
                env = proxy_environment({}, env)
        script = 'source "$CLASHCTL_HOME/scripts/cmd/clashctl.sh" || exit; '
        if args in (['start'], ['stop']):
            env['CLASH_PANEL_PYTHON'] = sys.executable
            env['CLASH_PANEL_CORE_SOURCE'] = globals().get('CORE_AUTOSTART_SOURCE') or Path(__file__).with_name('core_autostart.py').read_text(encoding='utf-8')
            # Recover an orphaned nohup core without changing upstream scripts. The
            # native lock and identity checks prevent adopting a reused/unrelated PID.
            namespace = {'__name__': 'clash_panel_core_service'}
            exec(compile(env['CLASH_PANEL_CORE_SOURCE'], 'core_autostart.py', 'exec'), namespace)
            script = namespace['CORE_SERVICE_SCRIPT']
        else:
            script += 'clashctl "$@"'
        try:
            process = subprocess.run(['bash', '--noprofile', '--norc', '-c', script, 'clash-panel'] + args, input='', capture_output=True, text=True, timeout=95, env=env)
        except subprocess.TimeoutExpired:
            raise ValueError('clashctl 执行超时。操作可能已部分完成，请刷新确认，再查看日志。') from None
        output = redact((process.stdout + '\n' + process.stderr)[-20000:], self.secrets)
        if process.returncode:
            raise ValueError(output.strip() or 'clashctl 操作失败。')
        if args in (['start'], ['stop']):
            self.verify_core_service(args == ['start'])
        return {'message': output.strip() or '操作完成。'}

    def dispatch(self, action, params):
        if action == 'snapshot':
            return self.snapshot()
        if action == 'terminalProxy':
            if type(params.get('enabled')) is not bool:
                raise ValueError('终端默认代理开关必须是布尔值。')
            if params['enabled'] and not any(port(self.config.get(key)) for key in ('port', 'mixed-port', 'socks-port')):
                raise ValueError('请先设置至少一个可用的代理监听端口。')
            with config_lock(self.root):
                return self.terminal_settings().set_enabled(params['enabled'])
        if action == 'select':
            group, node = valid_name(params.get('group')), valid_name(params.get('node'))
            groups = self.request('/proxies').get('proxies', {})
            item = groups.get(group, {})
            if item.get('type') != 'Selector' or node not in item.get('all', []):
                raise ValueError('只能切换手动选择组中的有效节点。')
            self.ensure_node_storage()
            self.request('/proxies/' + urllib.parse.quote(group, safe=''), 'PUT', {'name': node})
            selected = self.request('/proxies').get('proxies', {}).get(group, {}).get('now')
            if selected != node:
                raise ValueError('节点选择未生效，请刷新重试。')
            with config_lock(self.root):
                choices = self.saved_choices()
                profile = self.yaml('data/profiles.yaml').get('use', '')
                choices.setdefault(profile, {})[group] = node
                atomic_write(self.root / 'data/panel-selections.json', json.dumps(choices, ensure_ascii=False).encode())
            return {'message': '已选择 ' + node + '，Mihomo 会保存此手动选择。'}
        if action == 'mode':
            mode = params.get('mode')
            if mode not in ('rule', 'global', 'direct'):
                raise ValueError('无效的运行模式。')
            return self.apply_settings({'mode': mode}, '运行模式')
        if action == 'ports':
            return self.apply_settings(validate_ports(params.get('ports')), '代理端口')
        if action == 'tun':
            return self.set_tun(params.get('enabled'))
        if action == 'subDelete':
            return self.delete_profile(params)
        if action == 'delay':
            names = params.get('nodes')
            if not isinstance(names, list) or len(names) > 120:
                raise ValueError('每次最多测量 120 个节点。')
            known = self.request('/proxies').get('proxies', {})
            if any(not isinstance(n, str) or n not in known for n in names):
                raise ValueError('测速列表包含未知节点。')
            def measure(name):
                try:
                    result = self.request('/proxies/' + urllib.parse.quote(name, safe='') + '/delay?timeout=5000&url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204', timeout=7)
                    value = result.get('delay')
                    if type(value) is not int or value <= 0:
                        raise ValueError('测试未返回有效延迟。')
                    return name, value, None
                except Exception as exc:
                    return name, 0, redact(exc, self.secrets)
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(measure, dict.fromkeys(names)))
            return {'delays': {name: delay for name, delay, _ in results},
                    'errors': {name: error for name, _, error in results if error}}
        if action == 'connections':
            data = self.request('/connections')
            rows = []
            for item in (data.get('connections') or [])[:150]:
                meta = item.get('metadata') or {}
                rows.append({'host': meta.get('host') or meta.get('destinationIP') or '未知', 'port': meta.get('destinationPort', ''), 'network': meta.get('network', ''), 'chains': item.get('chains') or [], 'rule': item.get('rule', ''), 'upload': item.get('upload', 0), 'download': item.get('download', 0)})
            return {'connections': rows, 'count': len(data.get('connections') or []), 'uploadTotal': data.get('uploadTotal', 0), 'downloadTotal': data.get('downloadTotal', 0)}
        if action == 'logs':
            self.profiles()  # Load private URLs solely for redaction.
            log = self.root / 'data/mihomo.log'
            if not log.exists():
                return {'text': '暂无日志。'}
            with log.open('rb') as handle:
                handle.seek(max(0, log.stat().st_size - 48000))
                text = handle.read(48000).decode('utf-8', errors='replace')
            return {'text': redact('\n'.join(text.splitlines()[-120:]), self.secrets)}
        if action in ('start', 'stop'):
            return self.set_core_service(action == 'start')
        if action in ('subUse', 'subUpdate'):
            name = valid_name(params.get('name'))
            profiles = self.profiles()
            profile = next((p for p in profiles if p['name'] == name), None)
            if profile is None:
                raise ValueError('订阅不存在，请刷新。')
            if action == 'subDelete' and profile['active']:
                raise ValueError('正在使用的订阅不可删除，请先切换订阅。')
            result = self.cli(['sub', {'subUse': 'use', 'subUpdate': 'update'}[action], name])
            if action == 'subUse':
                if self.yaml('data/profiles.yaml').get('use') != name:
                    raise ValueError('当前订阅未保存成功，请刷新后检查。')
                result['message'] = '已启用并保存订阅“' + name + '”，下次打开继续使用。'
            return result
        if action in ('subAdd', 'subImport'):
            name = valid_name(params.get('name'))
            args = ['sub', 'add', '--name', name]
            if action == 'subImport':
                content = base64.b64decode(params.get('content', ''), validate=True)
                if not 0 < len(content) <= 2 * 1024 * 1024:
                    raise ValueError('YAML 文件需要非空且小于 2 MB。')
                imports = self.root / 'data/imports'
                imports.mkdir(mode=0o700, parents=True, exist_ok=True)
                target = imports / ('panel-' + uuid.uuid4().hex + '.yaml')
                with target.open('xb') as handle:
                    os.chmod(target, 0o600)
                    handle.write(content)
                url = target.as_uri()
                args.append('--raw')
            else:
                url = params.get('url')
                if not isinstance(url, str) or len(url) > 12000:
                    raise ValueError('订阅地址不正确。')
                parsed = urllib.parse.urlsplit(url)
                if parsed.scheme not in ('http', 'https') or not parsed.hostname or re.search(r'[\x00-\x20]', url):
                    raise ValueError('请输入有效的 HTTP(S) 订阅链接。本地文件请使用导入按钮。')
            self.secrets.append(url)
            args.append(url)
            try:
                return self.cli(args)
            except Exception:
                if action == 'subImport':
                    # Retain a source file if the CLI already committed its metadata.
                    saved = self.yaml('data/profiles.yaml').get('profiles') or []
                    if not any(item.get('url') == url for item in saved):
                        target.unlink(missing_ok=True)
                raise
        if action == 'terminalEnv':
            # Backend only: extension passes this straight to a terminal, never to its webview.
            env = proxy_environment(self.config, {})
            return {'env': env, 'home': str(self.root)}
        raise ValueError('不支持的操作。')


def main(payload):
    bridge = None
    try:
        bridge = Bridge(payload.get('home'))
        result = bridge.dispatch(payload.get('action'), payload.get('params') or {})
        return {'ok': True, 'data': result}
    except Exception as exc:
        return {'ok': False, 'error': redact(exc, bridge.secrets if bridge else ())}


if __name__ == '__main__':
    payload = globals().get('PAYLOAD')
    if payload is None:
        payload = json.load(sys.stdin)
    print(json.dumps(main(payload), ensure_ascii=False))

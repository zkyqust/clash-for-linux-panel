'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

function redact(value) {
  return String(value).replace(/\x1b\[[0-9;]*m/g, '')
    .replace(/(?:https?|socks5h?):\/\/[^\s<>"']+/gi, text => {
      try { const u = new URL(text); return `${u.protocol}//${u.hostname}${u.port ? ':' + u.port : ''}/[已隐藏]`; }
      catch { return '[链接已隐藏]'; }
    }).replace(/(Bearer\s+)\S+/gi, '$1[已隐藏]')
    .replace(/((?:secret|password|token|authorization)\s*[:=]\s*)[^\s,}]+/gi, '$1[已隐藏]');
}
function normalizeConfig(config, platform = process.platform) {
  const transport = config.transport === 'auto' ? (platform === 'win32' ? 'ssh' : 'local') : config.transport;
  if (!['local', 'ssh'].includes(transport)) throw new Error('不支持的连接方式。');
  if (typeof config.home !== 'string' || !/^(\/|~\/)/.test(config.home) || /[\x00-\x1f]/.test(config.home)) throw new Error('安装目录需要是 Linux 绝对路径或 ~/clashctl。');
  if (!/^(?:[A-Za-z0-9_.+-]+|\/[A-Za-z0-9_./+-]+)$/.test(config.python || '')) throw new Error('Python 命令格式不正确。');
  if (transport === 'ssh' && !/^(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(config.sshHost || '')) throw new Error('请先配置 SSH 主机，例如服务器别名或 user@192.168.1.2。');
  if (!Number.isInteger(config.sshPort) || config.sshPort < 1 || config.sshPort > 65535) throw new Error('SSH 端口必须在 1–65535 之间。');
  return { ...config, transport };
}
function sshArgs(config) {
  const args = ['-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2'];
  if (config.sshPort !== 22) args.push('-p', String(config.sshPort));
  return args.concat([config.sshHost]);
}
function collect(command, args, input, timeout = 120000) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '', stderr = '', settled = false;
    const finish = (err, val) => { if (settled) return; settled = true; clearTimeout(timer); err ? reject(err) : resolve(val); };
    const timer = setTimeout(() => { child.kill(); finish(new Error('操作超时，请检查 SSH 或 Clash 日志。正在执行的远端操作可能仍在继续，请刷新确认。')); }, timeout);
    const append = (kind, data) => {
      if (kind === 'out') stdout += data; else stderr += data;
      if (stdout.length + stderr.length > 6 * 1024 * 1024) { child.kill(); finish(new Error('输出超出安全大小限制。')); }
    };
    child.stdout.setEncoding('utf8').on('data', d => append('out', d));
    child.stderr.setEncoding('utf8').on('data', d => append('err', d));
    child.on('error', e => finish(new Error(redact(e.message))));
    child.stdin.on('error', () => {});
    child.on('close', code => {
      if (code !== 0) return finish(new Error(redact(stderr || stdout || `进程退出码 ${code}`)));
      try {
        const envelope = JSON.parse(stdout);
        if (!envelope.ok) throw new Error(envelope.error || '服务器操作失败。');
        finish(null, envelope.data);
      } catch (e) { finish(new Error(redact(e.message))); }
    });
    child.stdin.end(input);
  });
}
class Backend {
  constructor(root, configProvider) { this.root = root; this.configProvider = configProvider; }
  config() { return normalizeConfig(this.configProvider()); }
  async request(action, params = {}) {
    const config = this.config();
    const payload = { home: config.home, action, params };
    const timeout = ['tun', 'subDelete'].includes(action) ? 240000 : 120000;
    if (config.transport === 'ssh') {
      const helper = fs.readFileSync(path.join(this.root, 'bridge', 'terminal_proxy.py'), 'utf8');
      const coreHelper = fs.readFileSync(path.join(this.root, 'bridge', 'core_autostart.py'), 'utf8');
      const script = `import json,base64\nPAYLOAD=json.loads(base64.b64decode('${Buffer.from(JSON.stringify(payload)).toString('base64')}'))\nTERMINAL_PROXY_SOURCE=base64.b64decode('${Buffer.from(helper).toString('base64')}').decode('utf-8')\nCORE_AUTOSTART_SOURCE=base64.b64decode('${Buffer.from(coreHelper).toString('base64')}').decode('utf-8')\n` + fs.readFileSync(path.join(this.root, 'bridge', 'bridge.py'), 'utf8');
      return collect('ssh', sshArgs(config).concat([`${config.python} -`]), script, timeout);
    }
    if (process.platform === 'win32') throw new Error('Windows 本机请选择 SSH 方式，或在 Remote SSH 的 Linux 窗口中打开插件。');
    return collect(config.python, [path.join(this.root, 'bridge', 'bridge.py')], JSON.stringify(payload), timeout);
  }
}
module.exports = { Backend, redact, normalizeConfig, sshArgs, collect };

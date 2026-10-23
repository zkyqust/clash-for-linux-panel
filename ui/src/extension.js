'use strict';
const vscode = require('vscode');
const crypto = require('node:crypto');
const { Backend, redact, sshArgs } = require('./backend');

function configuration() {
  const cfg = vscode.workspace.getConfiguration('clashPanel');
  return Object.fromEntries(['transport', 'home', 'sshHost', 'sshPort', 'python', 'refreshSeconds'].map(k => [k, cfg.get(k)]));
}
class Controller {
  constructor(context) {
    this.context = context;
    this.backend = new Backend(context.extensionPath, configuration);
    this.views = new Set();
    this.sidebarViews = new Set();
    this.busy = false;
    this.testingNodes = new Set();
    this.refreshing = false;
    this.state = null;
    this.error = '';
    this.output = vscode.window.createOutputChannel('Clash Panel');
    this.lastRefresh = 0;
    this.activeTab = context.workspaceState.get('activeTab', 'overview');
    this.timer = setInterval(() => {
      if ([...this.views].some(v => v.visible) && Date.now() - this.lastRefresh > configuration().refreshSeconds * 1000) void this.refresh();
    }, 1000);
    context.subscriptions.push(this.output, { dispose: () => clearInterval(this.timer) });
  }
  attach(view, surface = 'dashboard') {
    view.webview.options = { enableScripts: true, localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, 'media')] };
    view.webview.html = this.html(view.webview, surface);
    this.views.add(view);
    if (surface === 'sidebar') this.sidebarViews.add(view);
    if (view.onDidChangeVisibility) this.context.subscriptions.push(view.onDidChangeVisibility(() => this.publish()));
    this.context.subscriptions.push(view.onDidDispose(() => { this.views.delete(view); this.sidebarViews.delete(view); }), view.webview.onDidReceiveMessage(message => { void this.handle(message); }));
    void this.refresh();
  }
  html(webview, surface) {
    const nonce = crypto.randomBytes(20).toString('base64');
    const asset = file => webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'media', file));
    return `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}'; img-src ${webview.cspSource} data:;"><link rel="stylesheet" href="${asset('style.css')}"><link rel="stylesheet" href="${asset('controls.css')}"><title>Clash Panel</title></head><body data-surface="${surface}"><div id="app"></div><script nonce="${nonce}" src="${asset('app.js')}"></script></body></html>`;
  }
  send(type, data) { for (const view of this.views) void view.webview.postMessage({ type, data }); }
  publish() {
    // Whitelisted snapshot only. Controller credentials and proxy environment never enter the webview.
    this.send('state', { snapshot: this.state, error: this.error, busy: this.busy, testingNodes: [...this.testingNodes], activeTab: this.activeTab, sidebarVisible: [...this.sidebarViews].some(v => v.visible), remote: vscode.env.remoteName || '', updated: this.lastRefresh });
  }
  async refresh() {
    if (this.busy || this.refreshing) return;
    this.refreshing = true;
    try { this.state = await this.backend.request('snapshot'); this.error = ''; }
    catch (e) { this.error = redact(e.message); this.state = null; }
    finally { this.lastRefresh = Date.now(); this.refreshing = false; this.publish(); }
  }
  async configure() {
    const selected = await vscode.window.showQuickPick([
      { label: '$(remote) 当前 Linux / Remote SSH 窗口', value: 'local', description: '直接读取该 Linux 的 ~/clashctl' },
      { label: '$(plug) 从本机通过 SSH 连接服务器', value: 'ssh', description: '适合 Windows 本地窗口；需要已配置的 SSH 密钥' }
    ], { title: 'Clash Panel · 连接方式' });
    if (!selected) return;
    const updates = { transport: selected.value };
    if (selected.value === 'ssh') {
      const host = await vscode.window.showInputBox({ title: 'SSH 主机', prompt: '输入 ~/.ssh/config 中的主机别名，或 user@hostname', value: configuration().sshHost, ignoreFocusOut: true, validateInput: x => /^(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(x) ? null : '请输入有效的 SSH 别名或 user@hostname。' });
      if (!host) return;
      const port = await vscode.window.showInputBox({ title: 'SSH 端口', prompt: '使用 SSH 别名时填 22，让 SSH 读取别名中的 Port。', value: String(configuration().sshPort), validateInput: x => /^\d+$/.test(x) && +x > 0 && +x <= 65535 ? null : '端口范围为 1–65535。' });
      if (!port) return;
      updates.sshHost = host; updates.sshPort = +port;
    }
    const home = await vscode.window.showInputBox({ title: 'Linux 上的 clashctl 目录', value: configuration().home, prompt: '例如 ~/clashctl 或 /home/用户名/clashctl', validateInput: x => /^(\/|~\/)/.test(x) && !/[\x00-\x1f]/.test(x) ? null : '请输入 Linux 绝对路径或 ~/clashctl。' });
    if (!home) return;
    updates.home = home;
    const cfg = vscode.workspace.getConfiguration('clashPanel');
    for (const [key, value] of Object.entries(updates)) await cfg.update(key, value, vscode.ConfigurationTarget.Global);
    await this.refresh();
  }
  async terminal() {
    const config = this.backend.config();
    let terminal;
    if (config.transport === 'ssh') {
      const args = sshArgs(config); args[0] = '-t';
      terminal = vscode.window.createTerminal({ name: 'Clash · 服务器终端', shellPath: 'ssh', shellArgs: args.concat('bash -il') });
    } else {
      const cleanEnv = Object.fromEntries(['http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'no_proxy', 'NO_PROXY'].map(key => [key, null]));
      terminal = vscode.window.createTerminal({ name: 'Clash · 服务器终端', shellPath: 'bash', shellArgs: ['-i'], env: cleanEnv });
    }
    terminal.show();
    this.context.subscriptions.push(terminal);
  }
  async measureDelay(message) {
    if (this.busy) return;
    let names = [];
    try {
      const group = this.state?.groups.find(g => g.name === message.group);
      if (!this.state?.running || !group) throw new Error('内核或策略组不可用，请刷新。');
      if (message.node !== undefined && (typeof message.node !== 'string' || !group.all.includes(message.node))) throw new Error('节点不在当前策略组中。');
      names = [...new Set(message.node === undefined ? group.all.slice(0, 120) : [message.node])].filter(n => !this.testingNodes.has(n));
      if (!names.length) return;
      for (const name of names) this.testingNodes.add(name);
      this.publish();
      const result = await this.backend.request('delay', { nodes: names });
      this.send('delay', result);
    } catch (e) {
      const text = redact(e.message);
      if (names.length) this.send('delay', { delays: Object.fromEntries(names.map(n => [n, 0])), errors: Object.fromEntries(names.map(n => [n, text])) });
      else this.send('notice', { text, error: true });
    } finally {
      for (const name of names) this.testingNodes.delete(name);
      this.publish();
    }
  }
  async handle(message) {
    if (!message || typeof message !== 'object' || typeof message.action !== 'string') return;
    if (message.action === 'ready') { this.publish(); if (!this.busy && !this.refreshing) await this.refresh(); return; }
    if (message.action === 'navigate') {
      if (!['overview', 'nodes', 'profiles', 'connections', 'logs', 'settings'].includes(message.tab)) return;
      this.activeTab = message.tab;
      await this.context.workspaceState.update('activeTab', this.activeTab);
      this.openDashboard?.();
      this.publish();
      return;
    }
    if (message.action === 'delay') { await this.measureDelay(message); return; }
    if (this.busy || this.refreshing) { this.send('notice', { text: '当前操作进行中，请稍候。', error: false }); return; }
    const action = message.action;
    if (action === 'ready' || action === 'refresh') { this.publish(); await this.refresh(); return; }
    const known = ['configure', 'terminal', 'start', 'stop', 'mode', 'ports', 'tun', 'terminalProxy', 'select', 'subAdd', 'subImport', 'subUse', 'subUpdate', 'subDelete', 'connections', 'logs'];
    if (!known.includes(action)) return;
    this.busy = true; this.publish();
    try {
      const params = {};
      if (action === 'configure') { await this.configure(); return; }
      if (action === 'terminal') { await this.terminal(); return; }
      if (action === 'stop') {
        const yes = await vscode.window.showWarningMessage('停止服务器 Clash？所有使用这个内核的程序都会失去代理连接。', { modal: true }, '停止服务器 Clash');
        if (!yes) return;
      }
      if (['subUse', 'subUpdate', 'subDelete'].includes(action)) {
        if (typeof message.name !== 'string' || !this.state?.profiles.some(p => p.name === message.name)) throw new Error('订阅不存在，请刷新。');
        params.name = message.name;
        if (action === 'subDelete') {
          const active = this.state.profiles.find(p => p.name === params.name).active;
          let detail = `删除订阅“${params.name}”及其配置？`;
          if (active) {
            const alternatives = this.state.profiles.filter(p => !p.active);
            params.allowActive = true;
            if (alternatives.length) {
              const choice = await vscode.window.showQuickPick(alternatives.map(p => ({ label: p.name, description: p.source })), { title: '删除当前订阅后，使用哪个订阅？', ignoreFocusOut: true });
              if (!choice) return;
              params.replacement = choice.label;
              detail += `\n将先切换到“${choice.label}”。`;
            } else {
              detail += '\n这是最后一个订阅。删除后将停止服务器 Clash，并清空当前订阅配置。';
            }
          }
          const yes = await vscode.window.showWarningMessage(detail, { modal: true }, active && !params.replacement ? '停止并删除' : '删除此订阅');
          if (!yes) return;
        }
      }
      if (action === 'subAdd' || action === 'subImport') {
        const name = await vscode.window.showInputBox({ title: '订阅名称', prompt: '添加后先保存，在订阅卡片中点击“启用”即可切换。', validateInput: x => x.trim() && !x.startsWith('-') && x.length <= 200 && !/[\x00-\x1f]/.test(x) ? null : '名称不能为空、以 - 开头或含控制字符。' });
        if (!name) return;
        params.name = name;
        if (action === 'subAdd') {
          const url = await vscode.window.showInputBox({ title: '订阅链接', password: true, ignoreFocusOut: true, prompt: 'HTTP(S) 链接仅交给服务器 clashctl，插件不保存到 VS Code 设置。', validateInput: x => { try { const u = new URL(x); return /^https?:$/.test(u.protocol) && u.hostname ? null : '请输入 HTTP(S) 链接。'; } catch { return '链接格式不正确。'; } } });
          if (!url) return;
          params.url = url;
        } else {
          const files = await vscode.window.showOpenDialog({ title: '导入 Clash YAML', canSelectMany: false, filters: { 'Clash YAML': ['yaml', 'yml'] } });
          if (!files?.[0]) return;
          const stat = await vscode.workspace.fs.stat(files[0]);
          if (!stat.size || stat.size > 2 * 1024 * 1024) throw new Error('文件需要非空且小于 2 MB。');
          params.content = Buffer.from(await vscode.workspace.fs.readFile(files[0])).toString('base64');
        }
      }
      if (action === 'mode') params.mode = message.mode;
      if (action === 'ports') params.ports = message.ports;
      if (action === 'terminalProxy') {
        if (typeof message.enabled !== 'boolean') throw new Error('终端默认代理开关参数不正确。');
        params.enabled = message.enabled;
      }
      if (action === 'tun') {
        if (typeof message.enabled !== 'boolean') throw new Error('TUN 开关参数不正确。');
        if (message.enabled && !this.state?.tunCapability?.canEnable) throw new Error(this.state?.tunCapability?.reason || '当前环境无法开启 TUN。');
        const yes = await vscode.window.showWarningMessage(`${message.enabled ? '开启' : '关闭'}目标 Linux 的 TUN 并保存？clashctl 会重启服务器内核，连接可能短暂中断。`, { modal: true }, '应用并保存');
        if (!yes) return;
        params.enabled = message.enabled;
      }
      if (action === 'select') { params.group = message.group; params.node = message.node; }
      const result = await this.backend.request(action, params);
      if (action === 'connections' || action === 'logs' || action === 'delay') this.send(action, result);
      else {
        this.send('notice', { text: result.message || '操作完成。', error: false });
        if (action === 'ports') this.send('portsSaved', {});
        this.output.appendLine(`[${new Date().toLocaleString()}] ${redact(result.message || action)}`);
      }
    } catch (e) {
      const text = redact(e.message);
      this.send('notice', { text, error: true });
      this.output.appendLine(text);
      void vscode.window.showErrorMessage('Clash Panel：' + text.slice(0, 600));
    } finally {
      this.busy = false;
      if (!['connections', 'logs', 'delay'].includes(action)) await this.refresh();
      else this.publish();
    }
  }
}

function activate(context) {
  const controller = new Controller(context);
  let panel;
  const open = () => {
    if (panel) { panel.reveal(); return; }
    panel = vscode.window.createWebviewPanel('clashPanel.dashboard', 'Clash Panel', vscode.ViewColumn.One, { enableScripts: true, retainContextWhenHidden: true });
    controller.attach(panel);
    panel.onDidDispose(() => { panel = undefined; });
  };
  controller.openDashboard = open;
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider('clashPanel.sidebar', { resolveWebviewView: view => controller.attach(view, 'sidebar') }, { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.commands.registerCommand('clashPanel.open', open),
    vscode.commands.registerCommand('clashPanel.configure', () => controller.handle({ action: 'configure' })),
    vscode.commands.registerCommand('clashPanel.refresh', () => controller.refresh()),
    vscode.commands.registerCommand('clashPanel.terminal', () => controller.handle({ action: 'terminal' })),
    vscode.commands.registerCommand('clashPanel.inspect', () => controller.backend.request('snapshot'))
  );
}
module.exports = { activate, Controller };

/* The webview has no network permissions. All operations use the extension's validated bridge. */
'use strict';
const vscode = acquireVsCodeApi();
const saved = vscode.getState() || {};
let model = { snapshot: null, busy: false, error: '', updated: 0 };
let tab = saved.tab || 'overview';
let selectedGroup = saved.group || '';
let search = '';
let connections = null;
let logText = '点击“读取日志”查看最近 120 行内核日志。';
let notice = null;
let delays = Object.create(null);
let delayErrors = Object.create(null);
let editingPorts = false;
let portDraft = null;
const sidebar = document.body.dataset.surface === 'sidebar';
const sections = [['overview', '◫', '概览'], ['nodes', '◈', '代理节点'], ['profiles', '▤', '订阅管理'], ['connections', '⇄', '活动连接'], ['logs', '≡', '运行日志'], ['settings', '⚙', '设置']];
const app = document.getElementById('app');
const el = (tag, className, text) => { const n = document.createElement(tag); if (className) n.className = className; if (text !== undefined) n.textContent = text; return n; };
const send = (action, params = {}) => vscode.postMessage({ action, ...params });
const bytes = n => { if (!n) return '0 B'; const i = Math.min(4, Math.floor(Math.log(n) / Math.log(1024))); return (n / 1024 ** i).toFixed(i ? 1 : 0) + [' B', ' KB', ' MB', ' GB', ' TB'][i]; };
function button(label, action, params = {}, kind = '') {
  const n = el('button', kind, label); n.type = 'button'; n.disabled = model.busy;
  n.addEventListener('click', () => send(action, params)); return n;
}
function empty(title, detail) { const n = el('div', 'empty'); n.append(el('div', 'empty-icon', '◌'), el('h3', '', title), el('p', 'muted', detail)); return n; }
function persist() { vscode.setState({ tab, group: selectedGroup }); }
function navigate(id) { tab = id; persist(); render(); send('navigate', { tab: id }); if (!sidebar && id === 'connections') send('connections'); if (!sidebar && id === 'logs') send('logs'); }
function renderSidebar() {
  app.replaceChildren();
  const shell = el('aside', 'sidebar-shell');
  const brand = el('div', 'brand sidebar-brand'); brand.append(el('div', 'mark', 'C'), el('div', '', 'CLASH PANEL')); shell.append(brand);
  const s = model.snapshot;
  const host = el('div', 'sidebar-host'); host.append(el('span', 'dot ' + (s?.running ? 'online' : 'offline')), el('span', '', s?.host || '尚未连接服务器')); shell.append(host);
  const nav = el('nav', 'sidebar-nav'); nav.setAttribute('aria-label', 'Clash 导航');
  for (const [id, icon, label] of sections) {
    const b = el('button', 'nav-item' + (tab === id ? ' active' : '')); b.type = 'button'; b.setAttribute('aria-label', label); b.setAttribute('aria-current', tab === id ? 'page' : 'false');
    b.append(el('span', 'nav-icon', icon), el('span', '', label));
    if (id === 'profiles' && s) b.append(el('span', 'nav-count', String(s.profiles.length)));
    b.onclick = () => navigate(id); nav.append(b);
  }
  shell.append(nav);
  const bottom = el('div', 'sidebar-bottom');
  bottom.append(button('＋ 新建服务器终端', 'terminal', {}, 'secondary'));
  bottom.append(el('p', 'muted small', '选择左侧栏目，在右侧控制台操作。'));
  if (model.error) bottom.append(el('p', 'warning small', model.error));
  shell.append(bottom); app.append(shell);
}
function portCard(s) {
  const card = el('section', 'status-card port-card');
  const head = el('div', 'section-head'); head.append(el('div', 'card-label', '服务器代理端口'));
  const edit = el('button', 'subtle edit-ports', editingPorts ? '取消' : '编辑'); edit.type = 'button'; edit.disabled = model.busy || !s.running;
  edit.onclick = () => { editingPorts = !editingPorts; portDraft = { http: s.ports.http || 0, mixed: s.ports.mixed || 0, socks: s.ports.socks || 0 }; render(); }; head.append(edit); card.append(head);
  if (editingPorts) {
    const form = el('form', 'port-form'); const grid = el('div', 'port-inputs');
    for (const [key, label] of [['http', 'HTTP'], ['mixed', '混合'], ['socks', 'SOCKS']]) {
      const field = el('label', 'port-field'); field.append(el('span', 'muted small', label));
      const input = el('input', 'port-input'); input.id = 'port-' + key; input.type = 'number'; input.min = '0'; input.max = '65535'; input.step = '1'; input.required = true; input.value = portDraft[key]; input.setAttribute('aria-label', label + '端口'); input.disabled = model.busy;
      input.oninput = () => { portDraft[key] = input.value; }; field.append(input); grid.append(field);
    }
    form.append(grid, el('p', 'muted small', '0 关闭端口。混合端口同时支持 HTTP 与 SOCKS。'));
    const error = el('p', 'form-error'); error.setAttribute('role', 'alert'); form.append(error);
    const save = el('button', 'primary', '保存并应用'); save.type = 'submit'; save.disabled = model.busy; form.append(save);
    form.onsubmit = event => {
      event.preventDefault();
      const ports = Object.fromEntries(Object.entries(portDraft).map(([k, v]) => [k, Number(v)]));
      const active = Object.values(ports).filter(Boolean);
      if (Object.values(portDraft).some(v => String(v).trim() === '') || Object.values(ports).some(v => !Number.isInteger(v) || v < 0 || v > 65535)) { error.textContent = '请输入 0–65535 的整数。'; return; }
      if (new Set(active).size !== active.length) { error.textContent = '独立监听端口不能重复。'; return; }
      error.textContent = ''; send('ports', { ports });
    };
    card.append(form);
  } else {
    const list = el('div', 'port-list');
    for (const [key, label] of [['http', 'HTTP'], ['mixed', '混合'], ['socks', 'SOCKS']]) {
      const p = el('div', 'port'); const value = el('button', 'port-value', s.ports[key] ? ':' + s.ports[key] : '未启用'); value.type = 'button'; value.disabled = model.busy || !s.running; value.setAttribute('aria-label', '编辑' + label + '端口'); value.onclick = () => edit.click(); p.append(el('span', 'muted', label), value); list.append(p);
    }
    card.append(list, el('p', 'muted small', '点击端口修改。保存到全局配置，更新订阅也会保留。'));
  }
  return card;
}
function modeCard(s) {
  const card = el('section', 'status-card'); card.append(el('div', 'card-label', '路由模式'));
  const modes = el('div', 'segmented');
  for (const [value, label] of [['rule', '规则'], ['global', '全局'], ['direct', '直连']]) {
    const b = button(label, 'mode', { mode: value }, s.mode.toLowerCase() === value ? 'selected' : ''); b.disabled ||= !s.running; modes.append(b);
  }
  card.append(modes, el('p', 'muted small', '自动保存。重新打开、重启内核或切换订阅后保留。')); return card;
}
function switchButton(label, checked, action, params) {
  const toggle = button('', action, params, 'switch' + (checked ? ' on' : ''));
  toggle.setAttribute('role', 'switch'); toggle.setAttribute('aria-label', label);
  toggle.setAttribute('aria-checked', String(checked)); toggle.append(el('span', 'switch-knob'));
  return toggle;
}
function kernelCard(s) {
  const card = el('section', 'status-card control-card');
  const enabled = s.running || s.coreAutostart?.enabled === true;
  const row = el('div', 'section-head'); row.append(el('h3', '', '内核服务'), switchButton('内核服务', enabled, enabled ? 'stop' : 'start', {}));
  const line = el('div', 'control-state'); line.append(el('span', 'dot ' + (s.running ? 'online' : 'offline')), el('strong', '', s.running ? '运行中' : '未连接'));
  card.append(row, line, el('p', 'muted small', s.version ? 'Mihomo ' + s.version : '启动内核后提供代理转发服务。'),
    el('p', 'muted small', s.coreAutostart?.enabled ? '已保存：新开的 Bash / Zsh 交互终端自动启动，已运行时跳过。' : '开启后同时启用新终端自动启动；关闭后取消。'));
  return card;
}
function terminalProxyCard(s) {
  const enabled = s.terminalProxy?.enabled === true;
  const card = el('section', 'status-card control-card');
  const row = el('div', 'section-head'); row.append(el('h3', '', '终端默认代理'), switchButton('终端默认代理', enabled, 'terminalProxy', { enabled: !enabled }));
  card.append(row, el('div', 'control-state', enabled ? (s.running ? '已开启' : '等待内核') : '已关闭'), el('p', 'muted small', '自动保存。当前用户新开的 Bash / Zsh 终端使用此设置。'));
  return card;
}
function tunCard(s) {
  const card = el('section', 'status-card control-card tun-card');
  const row = el('div', 'section-head');
  const label = el('div'); label.append(el('h3', '', 'TUN 虚拟网卡'));
  const toggle = switchButton('TUN 虚拟网卡', s.tun, 'tun', { enabled: !s.tun }); toggle.disabled ||= !s.running || (!s.tun && !s.tunCapability?.canEnable);
  row.append(label, toggle); card.append(row);
  card.append(el('div', 'control-state', s.tun ? (s.running ? '已开启' : '等待内核') : '已关闭'));
  if (!s.tun && !s.tunCapability?.canEnable) card.append(el('p', 'muted small', s.tunCapability?.reason || '当前环境暂不具备 TUN 条件。'));
  else card.append(el('p', 'muted small', '开关会保存到配置。流量走直连还是代理，由路由模式和规则决定。'));
  return card;
}
function render() {
  if (sidebar) { renderSidebar(); return; }
  const focus = document.activeElement?.id;
  const selection = focus === 'node-search' ? document.activeElement.selectionStart : null;
  app.replaceChildren();
  const wrap = el('main', 'shell');
  const header = el('header', 'header');
  const brand = el('div', 'page-heading');
  const section = sections.find(x => x[0] === tab) || sections[0]; brand.append(el('div', 'eyebrow', 'CLASH PANEL'), el('h1', '', section[2]));
  const actions = el('div', 'toolbar'); actions.append(button('↻ 刷新', 'refresh', {}, 'subtle'), button('连接设置', 'configure', {}, 'subtle'));
  header.append(brand, actions); wrap.append(header);
  const s = model.snapshot;
  const title = el('div', 'server-line'); title.append(el('span', 'dot ' + (s?.running ? 'online' : 'offline')), el('span', 'muted', s ? `${s.host} · ${s.home}` : '连接 Linux 上的 clashctl')); wrap.append(title);
  if (model.busy) { const busy = el('div', 'busy', '操作进行中，请稍候…'); busy.setAttribute('role', 'status'); wrap.append(busy); }
  if (notice) {
    const box = el('div', notice.error ? 'notice error' : 'notice'); box.setAttribute('role', 'status');
    const dismiss = el('button', 'dismiss', '×'); dismiss.setAttribute('aria-label', '关闭提示'); dismiss.onclick = () => { notice = null; render(); };
    box.append(el('span', '', notice.text), dismiss); wrap.append(box);
  }
  if (model.error || !s) {
    const box = empty('先连接你的 Clash', model.error || '正在读取服务器状态…');
    box.append(button('配置连接', 'configure', {}, 'primary'));
    const tip = el('p', 'muted help', 'Remote SSH 窗口：选择“当前 Linux”。Windows 本地窗口：选择“通过 SSH”，填入服务器主机及端口。SSH 需要密钥登录，并已确认主机指纹。'); box.append(tip); wrap.append(box);
  } else {
    for (const warning of s.warnings || []) wrap.append(el('div', 'warning', warning));
    if (s.apiError) { const detail = el('details', 'error-detail'); detail.append(el('summary', '', '查看控制接口错误详情'), el('p', 'muted small', s.apiError)); wrap.append(detail); }
    const tabs = el('nav', 'tabs'); tabs.setAttribute('aria-label', '控制台栏目');
    for (const [id, , label] of sections) {
      const b = el('button', tab === id ? 'tab active' : 'tab', label); b.type = 'button'; b.disabled = model.busy;
      b.onclick = () => navigate(id); tabs.append(b);
    }
    if (!model.sidebarVisible) wrap.append(tabs);
    const content = el('section', 'content');
    if (tab === 'overview') {
      const controls = el('section', 'control-grid'); controls.append(kernelCard(s), terminalProxyCard(s), tunCard(s)); content.append(controls);
      const bottom = el('div', 'overview-bottom'); bottom.append(modeCard(s));
      const active = s.profiles.find(p => p.active); const card = el('section', 'status-card'); card.append(el('div', 'card-label', '当前订阅'), el('h3', '', active?.name || '尚未启用订阅'), el('p', 'muted small', s.nodePersistence ? '订阅和手动节点选择均保留到下次打开。' : '订阅自动保留；首次选择节点时会启用节点保存。'));
      const manage = el('button', 'secondary', '管理订阅'); manage.type = 'button'; manage.disabled = model.busy; manage.onclick = () => navigate('profiles'); card.append(manage); bottom.append(card); content.append(bottom);
    }
    if (tab === 'settings') {
      const grid = el('div', 'settings-grid'); grid.append(portCard(s));
      const connection = el('section', 'status-card'); connection.append(el('h3', '', '管理连接'), el('p', 'muted small', s.host + ' · ' + s.home), button('修改连接', 'configure', {}, 'secondary'));
      grid.append(connection); content.append(grid, el('p', 'muted small', '内核、终端默认代理、TUN 与路由模式在“概览”中统一管理。端口修改保存后，新终端会读取新地址。'));
    }
    if (tab === 'nodes') renderNodes(content, s);
    if (tab === 'profiles') renderProfiles(content, s);
    if (tab === 'connections') renderConnections(content, s);
    if (tab === 'logs') { const row = el('div', 'section-head'); row.append(el('h2', '', '内核日志'), button('读取日志', 'logs', {}, 'secondary')); content.append(row, el('p', 'muted small', '显示最近 120 行。链接凭据与控制接口密钥会被隐藏。'), el('pre', 'logs', logText)); }
    wrap.append(content);
  }
  const footer = el('footer', 'footer'); footer.append(el('span', '', 'POWERED BY CLASHCTL · MIHOMO'), el('span', '', model.updated ? '更新于 ' + new Date(model.updated).toLocaleTimeString() : '等待连接')); wrap.append(footer);
  app.append(wrap);
  if (focus === 'node-search') { const target = document.getElementById(focus); target?.focus(); target?.setSelectionRange(selection, selection); }
  else if (focus?.startsWith('port-')) document.getElementById(focus)?.focus();
}
function renderNodes(parent, s) {
  if (!s.running) { parent.append(empty('等待内核连接', '启动内核或检查控制接口后，即可查看策略组。')); return; }
  if (!s.groups.length) { parent.append(empty('暂无策略组', '先在订阅中添加并启用一份配置。')); return; }
  if (!s.groups.some(g => g.name === selectedGroup)) selectedGroup = s.groups.find(g => g.selectable && g.name !== 'GLOBAL')?.name || s.groups[0].name;
  const group = s.groups.find(g => g.name === selectedGroup);
  const head = el('div', 'section-head');
  const picker = el('select', 'group-picker'); picker.setAttribute('aria-label', '选择策略组'); picker.disabled = model.busy;
  for (const g of s.groups) { const option = el('option', '', g.name); option.value = g.name; option.selected = g.name === selectedGroup; picker.append(option); }
  picker.onchange = () => { selectedGroup = picker.value; search = ''; persist(); render(); };
  const pending = new Set(model.testingNodes || []);
  const batch = button('整组测延迟', 'delay', { group: group.name }, 'secondary'); batch.disabled ||= group.all.some(name => pending.has(name));
  head.append(picker, batch); parent.append(head);
  const current = el('div', 'current-node'); current.append(el('span', 'muted small', group.selectable ? '当前选择' : '自动策略 · 由内核选择'), el('strong', '', group.now || '—')); parent.append(current);
  if (s.mode.toLowerCase() === 'global' && group.name !== 'GLOBAL') parent.append(el('p', 'warning small', '当前是全局模式，出口由 GLOBAL 组决定。'));
  if (s.mode.toLowerCase() === 'direct') parent.append(el('p', 'warning small', '当前是直连模式，节点选择不会改变当前直连行为。'));
  const input = el('input', 'search'); input.id = 'node-search'; input.placeholder = '搜索节点，例如：台湾 / 日本'; input.value = search; input.setAttribute('aria-label', '搜索节点'); input.oninput = () => { search = input.value; render(); }; parent.append(input);
  const nodes = el('div', 'node-grid');
  for (const name of group.all.filter(n => n.toLowerCase().includes(search.toLowerCase()))) {
    const meta = s.nodes[name] || {}; const isCurrent = name === group.now;
    const node = el('article', 'node' + (isCurrent ? ' current' : ''));
    const select = el('button', 'node-select'); select.type = 'button'; select.disabled = model.busy || !group.selectable;
    select.setAttribute('aria-label', `选择 ${name}`); select.setAttribute('aria-pressed', String(isCurrent));
    const label = el('div', 'node-label'); label.append(el('span', 'node-name', name), el('span', 'node-type', meta.type || '节点'));
    const delay = delays[name] ?? meta.delay;
    const measuring = pending.has(name);
    const value = measuring ? '测试中…' : delay === undefined || delay === null ? '未测试' : delay > 0 ? delay + ' ms' : '测试失败';
    select.append(el('span', 'radio', isCurrent ? '●' : '○'), label); select.onclick = () => send('select', { group: group.name, node: name });
    const tools = el('div', 'node-tools'); const latency = el('span', 'latency' + (delay > 1000 || delay === 0 ? ' slow' : ''), value); latency.setAttribute('aria-live', 'polite'); if (delayErrors[name]) latency.title = delayErrors[name];
    const test = button(measuring ? '测试中' : delay === 0 ? '重试' : '测延迟', 'delay', { group: group.name, node: name }, 'node-test'); test.setAttribute('aria-label', `测量 ${name} 的延迟`); test.disabled ||= measuring;
    tools.append(latency, test); node.append(select, tools); nodes.append(node);
  }
  if (!nodes.childElementCount) nodes.append(empty('没有匹配节点', '换一个关键词试试。'));
  parent.append(nodes, el('p', 'muted small', '节点选择自动保存。测延迟不会切换节点；自动策略组继续由内核决定出口。'), el('p', 'muted small', '测试目标：Google 连通性地址 · 超时 5 秒 · 延迟单位 ms，失败后可重试。'));
}
function renderProfiles(parent, s) {
  const head = el('div', 'section-head'); head.append(el('h2', '', '我的订阅'));
  const actions = el('div', 'row'); actions.append(button('导入 YAML', 'subImport', {}, 'secondary'), button('＋ 添加链接', 'subAdd', {}, 'primary')); head.append(actions); parent.append(head);
  parent.append(el('p', 'muted small', '添加后先保存，再点击启用。切换或更新当前订阅时，clashctl 可能重启内核。'));
  const grid = el('div', 'profile-grid');
  for (const p of s.profiles) {
    const card = el('article', 'profile' + (p.active ? ' active-profile' : '')); const title = el('div', 'section-head'); title.append(el('h3', '', p.name)); if (p.active) title.append(el('span', 'badge', '使用中'));
    card.append(title, el('p', 'muted', p.source), el('p', 'muted small', '更新：' + (p.updated || '暂无记录')));
    const row = el('div', 'row');
    if (!p.active) row.append(button('启用', 'subUse', { name: p.name }, 'primary'));
    row.append(button('更新', 'subUpdate', { name: p.name }, 'secondary'));
    row.append(button('删除', 'subDelete', { name: p.name }, 'subtle danger'));
    card.append(row); grid.append(card);
  }
  if (!s.profiles.length) grid.append(empty('还没有订阅', '添加链接，或从本地导入一份 Clash YAML。'));
  parent.append(grid);
}
function renderConnections(parent, s) {
  const head = el('div', 'section-head'); head.append(el('h2', '', '正在连接'), button('读取连接', 'connections', {}, 'secondary')); parent.append(head);
  if (!connections) { parent.append(empty('查看真实连接', '点击读取，查看请求匹配的规则与节点链路。')); return; }
  parent.append(el('p', 'muted small', `${connections.count} 个连接 · 累计上行 ${bytes(connections.uploadTotal)} / 下行 ${bytes(connections.downloadTotal)} · 最多展示 150 条`));
  for (const c of connections.connections) {
    const row = el('article', 'connection'); const top = el('div', 'section-head'); top.append(el('strong', '', `${c.host}:${c.port}`), el('span', 'pill', c.network));
    row.append(top, el('p', 'chain', (c.chains.length ? [...c.chains].reverse().join(' → ') : '—')), el('p', 'muted small', `${c.rule || '未记录规则'} · ↑ ${bytes(c.upload)} · ↓ ${bytes(c.download)}`)); parent.append(row);
  }
  if (!connections.connections.length) parent.append(empty('当前没有活动连接', '这是读取时的快照；发起请求后可再次读取。'));
}
window.addEventListener('message', event => {
  const { type, data } = event.data || {};
  if (type === 'state') {
    if (model.snapshot && data.snapshot && (model.snapshot.home !== data.snapshot.home || model.snapshot.host !== data.snapshot.host)) { delays = Object.create(null); delayErrors = Object.create(null); }
    model = data; if (sections.some(x => x[0] === data.activeTab)) tab = data.activeTab;
  }
  if (type === 'notice') notice = data;
  if (type === 'connections') connections = data;
  if (type === 'logs') logText = data.text;
  if (type === 'delay') { Object.assign(delays, data.delays || {}); for (const name of Object.keys(data.delays || {})) delete delayErrors[name]; Object.assign(delayErrors, data.errors || {}); }
  if (type === 'portsSaved') { editingPorts = false; portDraft = null; }
  render();
});
render(); send('ready');

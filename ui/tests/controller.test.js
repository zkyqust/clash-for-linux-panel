'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function controller() {
  const messages = [], requests = [];
  const vscode = {
    workspace: { getConfiguration: () => ({ get: k => ({ refreshSeconds: 10 }[k]) }) },
    window: { createOutputChannel: () => ({ appendLine() {}, dispose() {} }), showErrorMessage() {} },
    env: {}
  };
  const scope = { module: { exports: {} }, setInterval: () => 1, clearInterval() {},
    require: name => name === 'vscode' ? vscode : name === './backend' ? require('../src/backend') : require(name) };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/extension.js'), 'utf8'), scope);
  const context = { extensionPath: path.join(__dirname, '..'), subscriptions: [], workspaceState: { get: (_, v) => v, update: async () => {} } };
  const c = new scope.module.exports.Controller(context);
  c.state = { running: true, groups: [{ name: 'group', all: ['日本', '台湾'], selectable: true }] };
  c.send = (type, data) => messages.push({ type, data });
  c.backend.request = async (action, params) => { requests.push({ action, params }); return { delays: { 日本: 120 }, errors: {} }; };
  return { c, messages, requests };
}

test('single-node delay requests exactly that node and never selects it', async () => {
  const { c, requests } = controller();
  await c.handle({ action: 'delay', group: 'group', node: '日本' });
  assert.deepEqual(JSON.parse(JSON.stringify(requests)), [{ action: 'delay', params: { nodes: ['日本'] } }]);
  assert.equal(c.testingNodes.size, 0);
});

test('pending delay is visible without blocking the whole controller and duplicate tests are ignored', async () => {
  const { c, messages } = controller(); let finish;
  c.backend.request = () => new Promise(resolve => { finish = resolve; });
  const pending = c.handle({ action: 'delay', group: 'group', node: '日本' });
  assert.equal(c.busy, false); assert.equal(c.testingNodes.has('日本'), true);
  await c.handle({ action: 'delay', group: 'group', node: '日本' });
  assert.equal(c.testingNodes.has('日本'), true);
  finish({ delays: { 日本: 100 }, errors: {} }); await pending;
  assert.equal(c.testingNodes.size, 0); assert.ok(messages.some(m => m.type === 'delay'));
});

test('a node outside the current group cannot be tested', async () => {
  const { c, requests, messages } = controller();
  await c.handle({ action: 'delay', group: 'group', node: 'unknown' });
  assert.equal(requests.length, 0); assert.ok(messages.some(m => m.type === 'notice' && m.data.error));
});

test('failed test clears pending state and reports a node-specific failure', async () => {
  const { c, messages } = controller(); c.backend.request = async () => { throw new Error('connection refused'); };
  await c.handle({ action: 'delay', group: 'group', node: '日本' });
  assert.equal(c.testingNodes.size, 0);
  const result = messages.find(m => m.type === 'delay').data;
  assert.equal(result.delays['日本'], 0); assert.equal(result.errors['日本'], 'connection refused');
});

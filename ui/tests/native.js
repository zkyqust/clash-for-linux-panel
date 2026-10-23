'use strict';
const vscode = require('vscode');
const assert = require('node:assert/strict');
const fs = require('node:fs');
async function run() {
  const result = { kind: 'native-vscode', checks: [] };
  try {
    const extension = vscode.extensions.getExtension('local-tools.clashctl-panel');
    assert.ok(extension, 'development extension discovered');
    await extension.activate();
    assert.ok(extension.isActive); result.checks.push('extension activated');
    result.extensionVersion = extension.packageJSON.version;
    assert.equal(vscode.workspace.getConfiguration('clashPanel').inspect('home').defaultValue, '~/clashctl');
    result.checks.push('default installation directory is ~/clashctl');
    const commands = await vscode.commands.getCommands();
    for (const name of ['open', 'configure', 'refresh', 'terminal', 'inspect']) assert.ok(commands.includes('clashPanel.' + name));
    result.checks.push('commands registered');
    if (process.env.CLASHPANEL_TEST_HOST) {
      const cfg = vscode.workspace.getConfiguration('clashPanel');
      for (const [key, value] of Object.entries({ transport: 'ssh', sshHost: process.env.CLASHPANEL_TEST_HOST, sshPort: Number(process.env.CLASHPANEL_TEST_PORT || '22'), home: '~/clashctl' })) await cfg.update(key, value, vscode.ConfigurationTarget.Global);
      const state = await vscode.commands.executeCommand('clashPanel.inspect');
      assert.equal(typeof state.running, 'boolean');
      assert.ok(Array.isArray(state.profiles));
      assert.ok(Array.isArray(state.groups));
      assert.equal(typeof state.terminalProxy.enabled, 'boolean');
      assert.equal(typeof state.nodePersistence, 'boolean');
      assert.equal(typeof state.tunCapability.canEnable, 'boolean');
      result.server = { running: state.running, version: state.version, profiles: state.profiles.length, groups: state.groups.length, tunAvailable: state.tunAvailable };
      result.checks.push('real server read-only snapshot through extension host');
    }
    await vscode.commands.executeCommand('clashPanel.open');
    await vscode.commands.executeCommand('clashPanel.sidebar.focus');
    result.checks.push('dashboard and sidebar opened');
    result.ok = true;
  } catch (e) { result.ok = false; result.error = e.stack; throw e; }
  finally { if (process.env.CLASHPANEL_TEST_RESULT) fs.writeFileSync(process.env.CLASHPANEL_TEST_RESULT, JSON.stringify(result, null, 2)); }
}
module.exports = { run };

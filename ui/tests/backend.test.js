'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { normalizeConfig, sshArgs, redact, collect } = require('../src/backend');
const config = { transport: 'auto', home: '~/clashctl', sshHost: 'test@host.example', sshPort: 32767, python: 'python3' };
test('auto transport runs beside Linux workspace and uses SSH from Windows', () => {
  assert.equal(normalizeConfig(config, 'linux').transport, 'local');
  assert.equal(normalizeConfig(config, 'win32').transport, 'ssh');
});
test('rejects command and SSH option injection', () => {
  for (const sshHost of ['-oProxyCommand=evil', 'user@host;echo bad', 'user@host\nfoo', 'host arg']) assert.throws(() => normalizeConfig({ ...config, sshHost }, 'win32'));
  assert.throws(() => normalizeConfig({ ...config, python: 'python3;echo bad' }));
  assert.throws(() => normalizeConfig({ ...config, home: '/tmp\nmalformed' }));
  assert.throws(() => normalizeConfig({ ...config, sshPort: 0 }));
});
test('SSH verifies known host and requires noninteractive key authentication', () => {
  const args = sshArgs(config);
  assert.ok(args.includes('StrictHostKeyChecking=yes'));
  assert.ok(args.includes('BatchMode=yes'));
  assert.equal(args.at(-1), config.sshHost);
  assert.ok(args.includes('32767'));
  assert.ok(!sshArgs({ ...config, sshPort: 22 }).includes('-p'));
});
test('redacts subscription paths, queries, userinfo and authorization', () => {
  const result = redact('https://user:pass@service.test/s/private-token?key=secret Bearer abcxyz secret: rawSecret');
  for (const text of ['private-token', 'pass@', 'key=secret', 'abcxyz', 'rawSecret']) assert.ok(!result.includes(text));
  assert.ok(result.includes('service.test'));
});
test('child process decodes protocol and preserves Unicode', async () => {
  const value = await collect(process.execPath, ['-e', 'process.stdin.resume();process.stdin.on("end",()=>console.log(JSON.stringify({ok:true,data:{name:"台湾节点"}})))'], '{}');
  assert.equal(value.name, '台湾节点');
});
test('child process reports errors without exposing URL secrets', async () => {
  await assert.rejects(collect(process.execPath, ['-e', 'console.log(JSON.stringify({ok:false,error:"https://host.test/s/private"}))'], ''), error => !error.message.includes('private'));
});
test('child process has a bounded timeout', async () => {
  await assert.rejects(collect(process.execPath, ['-e', 'setInterval(()=>{},1000)'], '', 50), /超时/);
});

import assert from 'node:assert/strict';
import { copyFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = fileURLToPath(new URL('../', import.meta.url));
const assets = join(root, 'plugins/tokensand-agent-pack/skills/tokensand-agent-pack/assets');
const modules = resolve(process.env.VITEST_TEST_MODULES ?? join(root, 'node_modules'));
const vitest = join(modules, 'vitest/vitest.mjs');

test('native frontend suite checks the real workflow and keeps private output out of test logs', {
  skip: !existsSync(vitest) && 'Set VITEST_TEST_MODULES to an installed Vitest 3.2+ node_modules.',
}, t => {
  const app = mkdtempSync(join(tmpdir(), 'tokensand frontend-'));
  t.after(() => rmSync(app, {recursive: true, force: true}));
  const tests = join(app, 'src/test');
  const checker = join(app, 'node_modules/@tokensand/workflow-check');
  mkdirSync(checker, {recursive: true});
  mkdirSync(tests, {recursive: true});
  cpSync(assets, join(app, '.agents/skills/tokensand-agent-pack/assets'), {recursive: true});
  symlinkSync(join(modules, 'vitest'), join(app, 'node_modules/vitest'), 'dir');
  const archive = join(root, 'packages/tokensand-workflow-check-0.1.31.tgz');
  assert.equal(spawnSync('tar', ['-xzf', archive, '--strip-components=1', '-C', checker]).status, 0);
  copyFileSync(join(assets, 'workflow-test.ts.template'), join(tests, 'tokensand-workflow.test.ts'));
  copyFileSync(join(assets, 'tokensand-case-ids.mjs'), join(tests, 'tokensand-case-ids.mjs'));
  writeFileSync(join(tests,'existing.test.ts'), "import {test,expect} from 'vitest'; test('ordinary app test',()=>expect(1).toBe(1));");
  const secret = 'SYNTHETIC_PRIVATE_OUTPUT';
  const adapter = join(app, 'observe.workflow.mjs');
  function workflow(candidate, score = 'output === input') {
    writeFileSync(adapter, `export default {
      id:'routing', maxCallsPerRun:{candidate:0}, cases:[{id:'ordinary',input:0},{id:'sensitive-case-id',input:1}],
      checks:[{id:'routed',label:'Correct routing'}], candidate: input => {console.log('${secret}'); ${candidate}},
      evaluate:({output,input})=>({routed:${score}})
    };`);
  }
  function check(status, env={}) {
    const result=spawnSync(process.execPath,[vitest,'run','--reporter=verbose'],{
      cwd:app, encoding:'utf8', timeout:30000,
      env:{...process.env, CI:'', GITHUB_ACTIONS:'', ...env},
    });
    const output=result.stdout+result.stderr;
    assert.equal(result.status,status,output);
    assert.doesNotMatch(output,new RegExp(secret));
    assert.match(output,/ordinary app test/);
    assert.doesNotMatch(output,/\.agents\/skills\/[^\n]* > /);
    return output;
  }
  assert.match(check(1),/Create observe.workflow.mjs/);
  workflow("if(input) throw Error('" + secret + "'); return input;");
  assert.match(check(1),/1 passed, 1 failed, 0 unscored[\s\S]*Affected cases: \["sensitive-case-id"\]/);
  assert.doesNotMatch(check(1,{CI:'true'}),/sensitive-case-id/);
  workflow('return input;', 'input ? undefined : true');
  assert.match(check(1),/1 passed, 0 failed, 1 unscored/);
  workflow('return input;');
  assert.match(check(0),/2 passed, 0 failed, 0 unscored/);
  writeFileSync(adapter, "console.log('2 passed, 0 failed'); process.exit(0)");
  assert.match(check(1),/No complete Tokens& result/);
  const fork=check(0,{GITHUB_ACTIONS:'true',GITHUB_EVENT_NAME:'pull_request'});
  assert.match(fork,/skipped/);
  assert.doesNotMatch(fork,/2 passed, 0 failed/);
});

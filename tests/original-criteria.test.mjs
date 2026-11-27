import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const scripts = new URL('../plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/', import.meta.url);
const caseId = "-expedite / O'Brien (EU): lot #2";
const cases = JSON.stringify([{ id: caseId, input: { ordered: 9, available: 6 } }]);
const checks = JSON.stringify([{ id: 'stock-limit', label: 'Respect available units' }]);

for (const [language, interpreter, ext] of [['Node', process.execPath, 'mjs'], ['Python', 'python3', 'py']]) {
  test(language + ' replays an option-like case against the original fulfillment policy', t => {
    const dir = mkdtempSync(join(tmpdir(), 'pack-stock-policy-'));
    t.after(() => rmSync(dir, {recursive: true, force: true}));
    const path = join(dir, 'workflow.' + ext);
    const runner = fileURLToPath(new URL('check_workflow.' + ext, scripts));
    function check(units, criterion, expectedExit) {
      const adapter = ext === 'mjs'
        ? `export default {maxCallsPerRun:{candidate:0},cases:${cases},checks:${checks},candidate:()=>${units},evaluate:({input,output})=>({"stock-limit":${criterion}})};`
        : `workflow=dict(cases=${cases},checks=${checks},candidate=lambda item:${units},evaluate=lambda item,output,variant:{"stock-limit":${criterion}})`;
      writeFileSync(path, adapter);
      const run = spawnSync(interpreter, [runner, '--offline-workflow', path, '--case=' + caseId], {encoding:'utf8', timeout:10000});
      assert.ifError(run.error);
      const result = run.stdout + run.stderr;
      assert.equal(run.status, expectedExit, result);
      assert.ok(result.includes(caseId) && result.includes('trial 3/3'), result);
    }
    const original = ext === 'mjs' ? 'output <= input.available' : 'output <= item["available"]';
    check(9, 'output >= 0', 0);
    check(9, original, 1);
    check(6, original, 0);
  });
}

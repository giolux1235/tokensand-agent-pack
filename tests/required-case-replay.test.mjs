import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

test('the documented release command holds a selected note that omits a team-required case', t => {
  const root = new URL('../', import.meta.url);
  const read = path => readFileSync(new URL(path, root), 'utf8');
  const command = /npx --yes (https:\/\/github\.com\/giolux1235\/tokensand-agent-pack\/releases\/download\/workflow-check-[\d.]+\/tokensand-workflow-check-[\d.]+\.tgz) (--recheck-release \.\/review\.html --reference RELEASE_TAG --case=REQUIRED_CASE_1 --case=REQUIRED_CASE_2 --run)/;
  const [, archiveUrl, args] = read('README.md').match(command);
  const skill = read('plugins/tokensand-agent-pack/skills/tokensand-agent-pack/SKILL.md');
  assert.ok(skill.includes(archiveUrl + ' ' + args));
  const dir = mkdtempSync(join(tmpdir(), 'tokensand-required-cases-'));
  t.after(() => rmSync(dir, {recursive:true, force:true}));
  execFileSync('tar', ['-xzf', fileURLToPath(new URL('packages/' + basename(archiveUrl), root)), '-C', dir]);
  const note = {
    kind:'tokensand-release-note', schemaVersion:1, selection:'selected-cases',
    referenceRevision:'a'.repeat(40), candidateRevision:'b'.repeat(40),
    cases:[{id:'currency-usd',verdict:'still_passing',reference:{passed:3,total:3},candidate:{passed:3,total:3}}],
  };
  writeFileSync(join(dir, 'review.html'), '<script id="tokensand-release-note" type="application/json">' + JSON.stringify(note) + '</script>');
  const substitutions = {RELEASE_TAG:'accepted-currency','--case=REQUIRED_CASE_1':'--case=currency-usd','--case=REQUIRED_CASE_2':'--case=currency-jpy'};
  const result = spawnSync(process.execPath, [join(dir, 'package/cli.mjs'), ...args.split(' ').map(a => substitutions[a] ?? a)], {
    cwd:dir, env:{PATH:process.env.PATH}, encoding:'utf8',
  });
  assert.equal(result.status, 2);
  const report = JSON.parse(result.stdout);
  assert.equal(report.verdict, 'unknown');
  assert.match(report.reason, /[Cc]ase.*note/);
});

import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

test('release review sends only known reference failures to the packaged repair reviewer', t => {
  const dir = mkdtempSync(join(tmpdir(), 'tokensand-reference-recovery-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  let source = dirname(fileURLToPath(import.meta.url));
  if (!existsSync(join(source, 'release-note.mjs'))) {
    const archive = join(source, '../packages/tokensand-workflow-check-0.1.24.tgz');
    execFileSync('tar', ['-xzf', archive, '-C', dir]);
    source = join(dir, 'package');
  }
  for (const file of ['cli.mjs', 'release-note.mjs', 'review-repair-v1.mjs']) {
    copyFileSync(join(source, file), join(dir, file));
  }
  const marker = 'PRIVATE_WORKER_DETAIL';
  writeFileSync(join(dir, 'review-release-reference-v2.mjs'), `
    const id = process.argv.find(a => a.startsWith('--case=')).slice(7);
    if (id === 'healthy') {
      const arm = {passed:3, total:3};
      console.log(JSON.stringify({kind:'local-release-reference', case:id, verdict:'still_passing',
        reference:{...arm, revision:'a'.repeat(40)}, candidate:{...arm,revision:'b'.repeat(40)}}));
    } else {
      const reason = 'The selected reference case was already failing.' + (id === 'other' ? ' ${marker}' : '');
      console.log(JSON.stringify({kind:'local-release-reference', verdict:'unknown', reason}));
      process.exitCode = 2;
    }
  `);
  const command = (...args) => spawnSync(process.execPath, [join(dir, 'cli.mjs'), ...args], {
    cwd: dir, encoding: 'utf8', timeout: 10000, env: { PATH: process.env.PATH },
  });
  const release = (id, file) => command('--review-release','before','after','--reference','stable',
    '--case='+id,'--note',file);
  const broken = release('-pending','blocked.json');
  assert.equal(broken.status, 2, broken.stderr);
  assert.match(broken.stdout, /The selected reference case was already failing\./);
  assert.match(broken.stdout, /^--review-repair REFERENCE_RUN CANDIDATE_RUN --case -pending --original-checkout EARLIER_SERVICE$/m);
  assert.match(broken.stdout, /original checks/);
  assert.match(broken.stdout, /only have the saved runs, omit --original-checkout EARLIER_SERVICE/);
  assert.doesNotMatch(broken.stdout, /Open the clean candidate checkout/);
  assert.equal(existsSync(join(dir,'blocked.json')), false);
  const other = release('other','unknown.json');
  assert.equal(other.status, 2);
  assert.match(other.stdout, /Open the clean candidate checkout/);
  assert.doesNotMatch(other.stdout + other.stderr, /PRIVATE_WORKER_DETAIL|--review-repair|already failing/);
  assert.equal(existsSync(join(dir,'unknown.json')), false);
  const valid = release('healthy','healthy.json');
  assert.equal(valid.status, 0);
  assert.doesNotMatch(valid.stdout, /--review-repair/);
  assert.equal(JSON.parse(readFileSync(join(dir,'healthy.json'))).cases[0].verdict, 'still_passing');
  const repair = command('--review-repair');
  assert.equal(repair.status, 1);
  assert.match(repair.stderr, /Usage: node review-repair-v1\.mjs/);
  assert.doesNotMatch(repair.stdout, /append --original-checkout/);
  writeFileSync(join(dir, 'review-repair-v1.mjs'), "process.exitCode = process.argv[2] === 'failed' ? 100 : 0;");
  for (const [before, exit] of [['before',0], ['failed',100]]) {
    const recorded = command('--review-repair', before, 'after', '--case', 'pending');
    assert.equal(recorded.status, exit);
    assert.match(recorded.stdout, /append --original-checkout EARLIER_SERVICE/);
    const rescored = command('--review-repair', before, 'after', '--case', 'pending', '--original-checkout', 'earlier');
    assert.equal(rescored.status, exit);
    assert.doesNotMatch(rescored.stdout, /append --original-checkout/);
  }
});

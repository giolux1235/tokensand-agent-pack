import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const sourceCli = fileURLToPath(new URL('./cli.mjs', import.meta.url));
const env = { PATH: process.env.PATH, GIT_TERMINAL_PROMPT: '0' };

test('a recipient can verify several selected cases against the original scorer', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'tokensand-recipient-cases-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const cli = existsSync(sourceCli) ? sourceCli : join(dir, 'package', 'cli.mjs');
  if (!existsSync(sourceCli)) {
    const archive = fileURLToPath(new URL('../packages/tokensand-workflow-check-0.1.22.tgz', import.meta.url));
    execFileSync('tar', ['-xzf', archive, '-C', dir]);
  }
  const repo = join(dir, 'service');
  const notePath = join(dir, 'received.json');
  const observed = join(dir, 'callback-invoked');
  execFileSync('git', ['init', '-q', repo], { env });
  const git = (...args) => execFileSync('git', args, { cwd:repo, env, encoding:'utf8' }).trim();

  function revision(message, broken, repetitions = 3) {
    const workflow = `import { existsSync, writeFileSync } from 'node:fs';
      export default {
        id:'ticket-router', timeoutMs:3000, repetitions:${repetitions}, maxCallsPerRun:{baseline:0,candidate:0},
        cases:[{id:'blocked',input:1},{id:'clear',input:2},{id:'inconclusive',input:${broken ? 4 : 3}}],
        checks:[{id:'correct'}],
        candidate(input, ctx) {
          writeFileSync(${JSON.stringify(observed)},'yes');
          if (input === 1) writeFileSync('case-local-state','touched');
          if (input === 2 && existsSync('case-local-state')) return false;
          return ${broken ? 'input !== 1 && !(input === 2 && ctx.repetition === 4)' : 'true'};
        },
        evaluate: ${broken ? '()=>({correct:true})' : '({output})=>({correct:output === true})'}
      };`;
    writeFileSync(join(repo, 'observe.workflow.mjs'), workflow);
    git('add', 'observe.workflow.mjs');
    git('-c','user.name=Giovanni Amenta','-c','user.email=giovanni@creatorscorner.co','-c','commit.gpgsign=false','commit','-qm',message);
    return git('rev-parse','HEAD');
  }

  const referenceRevision = revision('Reference for recipient replay', false);
  const blockedRevision = revision('Break routing and weaken the candidate scorer', true);
  const cases = ['blocked','clear','inconclusive'].map(id => ({
    id, verdict:'still_passing', reference:{passed:3,total:3}, candidate:{passed:3,total:3}
  }));
  const note = {kind:'tokensand-release-note',schemaVersion:1,selection:'selected-cases',
    referenceRevision,candidateRevision:blockedRevision,cases};
  function save(document=note) { writeFileSync(notePath, JSON.stringify(document)); }
  function run(args, code, cwd=repo) {
    const result=spawnSync(process.execPath,[cli,'--recheck-release',notePath,...args],
      {cwd,env,encoding:'utf8',timeout:30000});
    assert.equal(result.status,code,result.stdout+result.stderr);
    assert.equal(result.stderr,'');
    return JSON.parse(result.stdout);
  }
  const selected = (...names) => names.flatMap(name => ['--case',name]);
  const verdicts = result => Object.fromEntries(result.cases.map(row => [row.case,row.verdict]));
  save();

  await t.test('preview lists the selection in requested order outside Git and invokes no callback', () => {
    const result=run(selected('clear','blocked'),2,dir);
    assert.equal(result.status,'awaiting_replay');
    assert.deepEqual(result.cases.map(row=>row.case),['clear','blocked']);
    assert.equal(result.verdict,undefined);
    assert.equal(run(['--all-cases'],2,dir).cases.length,3);
    assert.equal(existsSync(observed),false);
  });

  await t.test('validates every requested claim, cap, and option prior to execution', () => {
    for (const args of [
      selected('blocked','blocked'), selected('clear','absent'), ['--all-cases','--case','clear'],
      ['--all-cases','--all-cases'], ['--case','clear','--run','--run'],
      ['--case','clear','--reference','HEAD','--reference',referenceRevision],
      selected(...Array.from({length:21},(_,i)=>'case'+i))
    ]) {
      assert.equal(run([...args,'--run'],2).verdict,'unknown');
      assert.equal(existsSync(observed),false);
    }
    save({...note,cases:[cases[0],{...cases[1],candidate:{passed:1,total:3}}]});
    assert.equal(run(['--all-cases','--run'],2).verdict,'unknown');
    for (const total of [2, 11, 3.5, '5']) {
      const score = {passed:total,total};
      save({...note,cases:[{...cases[1],reference:score,candidate:score}]});
      assert.equal(run(['--all-cases','--run'],2).verdict,'unknown');
    }
    assert.equal(existsSync(observed),false);
    save();
  });

  await t.test('fresh checks block in either requested order and preserve per-case state isolation', () => {
    for (const order of [['blocked','clear'],['clear','blocked']]) {
      const result=run([...selected(...order),'--run'],100);
      assert.equal(result.verdict,'regressed');
      assert.deepEqual(result.cases.map(row=>row.case),order);
      assert.deepEqual(verdicts(result),{blocked:'regressed',clear:'still_passing'});
      const blocked=result.cases.find(row=>row.case==='blocked');
      assert.equal(blocked.agreement,'different_verdict');
      assert.deepEqual(blocked.reference,{passed:3,total:3});
      assert.deepEqual(blocked.candidate,{passed:0,total:3});
    }
  });

  await t.test('covers all listed cases and distinguishes a blocking case from an incomplete one', () => {
    const all=run(['--all-cases','--run'],100);
    assert.deepEqual(verdicts(all),{blocked:'regressed',clear:'still_passing',inconclusive:'unknown'});
    assert.equal(all.verdict,'regressed');
    const unresolved=run([...selected('inconclusive','clear'),'--run'],2);
    assert.equal(unresolved.verdict,'unknown');
    assert.deepEqual(verdicts(unresolved),{inconclusive:'unknown',clear:'still_passing'});
  });

  await t.test('replays the recorded trial count for each case, including a failure on the fifth call', () => {
    for (const total of [5, 10]) {
      save({...note,cases:[cases[0],{...cases[1],verdict:'regressed',
        reference:{passed:total,total},candidate:{passed:total-1,total}}]});
      const result=run(['--all-cases','--run'],100);
      assert.deepEqual(result.cases[0].reference,{passed:3,total:3});
      assert.deepEqual(result.cases[1].reference,{passed:total,total});
      assert.deepEqual(result.cases[1].candidate,{passed:total-1,total});
      assert.equal(result.cases[1].verdict,'regressed');
    }
    save();
  });

  await t.test('holds a recorded regression when the same revision passes only in isolation', () => {
    save({...note,cases:[{...cases[1],verdict:'regressed',candidate:{passed:2,total:3}}]});
    const result=run([...selected('clear'),'--run'],2);
    assert.equal(result.verdict,'unknown');
    assert.equal(result.agreement,'different_verdict');
    assert.deepEqual(result.candidate,{passed:3,total:3});
    assert.match(result.reason,/full workflow/i);
    save();
  });

  await t.test('verified full repair and unchanged single-case replay return success', () => {
    note.candidateRevision=revision('Restore the routing and its original inputs', false);save();
    const result=run(['--all-cases','--run'],0);
    assert.equal(result.verdict,'still_passing');
    assert.deepEqual(verdicts(result),{blocked:'still_passing',clear:'still_passing',inconclusive:'still_passing'});
    const single=run([...selected('blocked'),'--run'],0);
    assert.equal(single.case,'blocked');assert.equal(single.cases,undefined);
    assert.deepEqual(single.candidate,{passed:3,total:3});
    save({...note,cases:[{...cases[1],reference:{passed:7,total:7},candidate:{passed:7,total:7}}]});
    const repaired=run([...selected('clear'),'--run'],0);
    assert.deepEqual(repaired.reference,{passed:7,total:7});
    assert.deepEqual(repaired.candidate,{passed:7,total:7});
    assert.equal(git('status','--porcelain'),'');
  });

  await t.test('a note that understates the committed trial count cannot clear a late failure', () => {
    note.candidateRevision=revision('Record five routing repetitions', true, 5);
    save({...note,cases:[cases[1]]});
    const shorter=run([...selected('clear'),'--run'],2);
    assert.equal(shorter.verdict,'unknown');
    assert.match(shorter.reason,/repetitions declared in the committed workflow/i);
    save({...note,cases:[{...cases[1],verdict:'regressed',reference:{passed:5,total:5},candidate:{passed:4,total:5}}]});
    const complete=run([...selected('clear'),'--run'],100);
    assert.deepEqual(complete.candidate,{passed:4,total:5});
  });
});

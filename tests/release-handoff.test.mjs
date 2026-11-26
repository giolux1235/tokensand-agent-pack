import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync,spawnSync} from 'node:child_process';
import {existsSync,mkdtempSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const archive = ['./distribution/', '../packages/'].map(prefix =>
  fileURLToPath(new URL(prefix + 'tokensand-workflow-check-0.1.14.tgz', import.meta.url))).find(existsSync);
const privateMarker='RECIPIENT_PRIVATE_VALUE';
const env={PATH:process.env.PATH,HOME:process.env.HOME,GIT_TERMINAL_PROMPT:'0'};

test('check a deliberately shared note in a fresh self-contained service',async t=>{
  const dir=mkdtempSync(join(tmpdir(),'tokensand-hand-off-test-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
  execFileSync('tar',['-xzf',archive,'-C',dir]);
  const runner=join(dir,'package','cli.mjs');
  const core=join(dir,'package','agent-check.mjs');
  const file=join(dir,'note.json'), html=join(dir,'note.html'),repo=join(dir,'repo');
  function git(...args){return execFileSync('git',args,{cwd:repo,env,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();}
  execFileSync('git',['init','-q',repo],{env});
  const write=(path,bytes)=>writeFileSync(join(repo,path),bytes);
  const commit=(name)=>{git('add','.');git('-c','user.name=Giovanni Amenta','-c','user.email=giovanni@creatorscorner.co','-c','commit.gpgsign=false','commit','-qm',name);return git('rev-parse','HEAD');};
  const fixture=(evaluator,expected=2)=>`import {run} from './app.mjs';export default {id:'tiny-release',cases:['tie','-page'].map(id=>({id,input:{n:2,expected:${expected},private:'${privateMarker}'}})),checks:[{id:'correct'}],maxCallsPerRun:{baseline:0,candidate:0},timeoutMs:4000,candidate:run,evaluate:${evaluator}};`;
  const genuine='({input,output})=>({correct:output===input.expected})';
  write('app.mjs',`export const run=async i=>{console.error('${privateMarker}');return i.n};`);write('observe.workflow.mjs',fixture(genuine));
  const ref=commit('Reference for synthetic recipient test');git('tag','trusted-release');
  write('app.mjs','export const run=async i=>i.n-1;');write('observe.workflow.mjs',fixture('()=>({correct:true})'));
  let candidate=commit('Break synthetic application and weaken its scorer');
  const claim=(verdict='regressed',overrides={})=>({kind:'tokensand-release-note',schemaVersion:1,selection:'selected-cases',referenceRevision:ref,candidateRevision:candidate,
    cases:[{id:'tie',verdict,reference:{passed:3,total:3},candidate:{passed:verdict==='regressed'?0:3,total:3},reason:privateMarker}],...overrides});
  const save=(n)=>writeFileSync(file,JSON.stringify(n));
  function check(args,expected,cwd=repo,path=file,id='tie'){
    const res=spawnSync(process.execPath,[runner,'--recheck-release',path,'--case='+id,...args],{env,cwd,encoding:'utf8',timeout:30000});
    assert.equal(res.status,expected,res.stdout+res.stderr);assert.equal(res.stderr,'');assert.ok(!res.stdout.includes(privateMarker)&&!res.stdout.includes(dir));return JSON.parse(res.stdout);
  }
  await t.test('preview works outside Git and suppresses extras',()=>{save(claim());const r=check([],2,dir);assert.equal(r.status,'awaiting_replay');assert.equal(r.verdict,undefined);});
  await t.test('fresh check catches the same bug a weakened candidate scorer passed',()=>{
    const weak=spawnSync(process.execPath,[core,'--offline-workflow','./observe.workflow.mjs','--case','tie'],{cwd:repo,env,encoding:'utf8'});
    assert.equal(weak.status,0);
    save(claim('still_passing'));const r=check(['--run','--reference','trusted-release'],100);
    assert.equal(r.reference.passed,3);assert.equal(r.candidate.passed,0);assert.equal(r.agreement,'different_verdict');
  });
  await t.test('replays a leading-hyphen case and confirms a scored regression within three of five trials',()=>{
    const note=claim();note.cases=[{id:'-page',verdict:'regressed',reference:{passed:5,total:5},candidate:{passed:0,total:5}}];save(note);
    const r=check(['--run'],100,repo,file,'-page');
    assert.equal(r.verdict,'regressed');assert.equal(r.historicalTrialsPerRevision,5);
    assert.deepEqual(r.reference,{passed:3,total:3});assert.deepEqual(r.candidate,{passed:0,total:3});
  });
  await t.test('HTML is read as inert text and ambiguous notes are rejected',()=>{
    const marker='<script id="tokensand-release-note" type="application/json">',body=marker+JSON.stringify(claim())+'</script>';
    writeFileSync(html,'<html><body><p>'+privateMarker+'</p>'+body+'</body></html>');
    assert.equal(check(['--run'],100,repo,html).verdict,'regressed');
    for(const payload of ['<p>empty</p>',body+body,marker+'broken','s'.repeat(128*1024+1)]) {
      writeFileSync(html,payload);assert.equal(check(['--run'],2,repo,html).verdict,'unknown');
    }
  });
  await t.test('rejects wrong Git pair, malformed claims and unsafe IDs',()=>{
    save(claim());assert.equal(check(['--reference','HEAD','--run'],2).verdict,'unknown');
    save(claim('regressed',{candidateRevision:'2'.repeat(40)}));check(['--run'],2);
    save(claim('regressed',{referenceRevision:'3'.repeat(40)}));check(['--run'],2);
    const missingSelection=claim();delete missingSelection.selection;save(missingSelection);check([],2);
    const n=claim();n.cases.push(n.cases[0]);save(n);check(['--run'],2);
    const bad=claim();bad.cases[0].candidate.passed=3;save(bad);check(['--run'],2);
    const secret=claim();secret.cases[0].id='sk-synthetic-test';save(secret);check(['--run'],2);
    save(claim());const p=spawnSync(process.execPath,[runner,'--recheck-release',file,'--case','bad input/','--run'],{cwd:repo,env,encoding:'utf8'});assert.equal(p.status,2);assert.ok(!p.stdout.includes('bad input'));
  });
  await t.test('recognizes a real repair even when the note still claims regression',()=>{
    write('app.mjs','export const run=async i=>i.n;');candidate=commit('Repair the synthetic implementation');save(claim());
    const r=check(['--run'],0);assert.equal(r.verdict,'still_passing');assert.equal(r.agreement,'different_verdict');
  });
  await t.test('a green three-trial replay cannot clear a failure on the fifth recorded repetition',()=>{
    write('app.mjs','export const run=async (i,ctx)=>ctx.repetition===4?i.n-1:i.n;');candidate=commit('Fail only the fifth repetition');
    const note=claim();note.cases[0].reference={passed:5,total:5};note.cases[0].candidate={passed:4,total:5};save(note);
    const r=check(['--run'],2);
    assert.equal(r.verdict,'unknown');assert.equal(r.agreement,'inconclusive');assert.equal(r.historicalTrialsPerRevision,5);
    assert.deepEqual(r.reference,{passed:3,total:3});assert.deepEqual(r.candidate,{passed:3,total:3});
    assert.match(r.reason,/fewer repetitions/);
  });
  await t.test('withholds a verdict when one scored failure accompanies a separate timed-out trial',()=>{
    write('app.mjs',"export const run=async (i,ctx)=>{if(ctx.repetition===1)Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,4100);return ctx.repetition===0?i.n-1:i.n};");candidate=commit('Mix a real failure and a timeout');save(claim());
    assert.equal(check(['--run'],2).verdict,'unknown');
  });
  await t.test('withholds verdict if input or offline setup changed',()=>{
    write('observe.workflow.mjs',fixture(genuine,9));candidate=commit('Change synthetic input');save(claim());check(['--run'],2);
    write('observe.workflow.mjs',"import 'missing-offline-dependency';\n"+fixture(genuine));candidate=commit('Require an unavailable dependency');save(claim());check(['--run'],2);
  });
});

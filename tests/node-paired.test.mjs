import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const runner = fileURLToPath(new URL('../plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/check_workflow.mjs', import.meta.url));
const setup = `
let seen = 0;
const fn = (x) => { x.n++; return ++seen; };
export default {
  cases:[{id:'picked',input:{n:0}},{id:'other',input:{n:0}}], checks:[{id:'stock'},{id:'missing'}],
  repetitions:1,timeoutMs:2000,maxCallsPerRun:{baseline:0,candidate:0},
  baseline:fn,candidate:fn,
  evaluate:({input,output,variant})=>({stock:()=>variant==='candidate',missing:input.n===0 && output>0}),
  `;
function run(t, overrides='', options=['--compare-baseline','--case=picked']) {
  const cwd=mkdtempSync(join(tmpdir(),'paired-node-'));
  t.after(()=>rmSync(cwd,{force:true,recursive:true}));
  const adapter=join(cwd,'workflow.mjs');
  writeFileSync(adapter,setup+overrides+'};');
  const child=spawnSync(process.execPath,[runner,'--offline-workflow',adapter,...options],{cwd,encoding:'utf8',timeout:14000,
    env:{PATH:process.env.PATH,HOME:cwd,TMPDIR:cwd,NO_COLOR:'1'}});
  assert.ifError(child.error);
  return {code:child.status, text:child.stdout+child.stderr};
}
test('recovers named checks with identical state, independent inputs and isolated arm processes',t=>{
  const got=run(t,`evaluate:({input,output,variant})=>({stock:()=>variant==='candidate',missing:input.n===0 && output>=1 && output<=3})`);
  assert.equal(got.code,0,got.text);
  assert.match(got.text,/Recovered: "picked" \/ "stock" · trials 1, 2, 3/);
  assert.match(got.text,/Passed both: "picked" \/ "missing" · trials 1, 2, 3/);
  assert.doesNotMatch(got.text,/other/);
});
test('names new, existing, and incomplete checks without revealing raw app errors or output',t=>{
  const data=`baseline:()=>{console.error('sensitive-app-log');return 'private value'},candidate:()=>{console.log('sensitive-app-log');return 'private value'},
    evaluate:({variant})=>({stock:variant==='baseline',missing:()=>{throw Error('private thrown error')}})`;
  const got=run(t,data);assert.equal(got.code,1,got.text);
  assert.match(got.text,/New failure: "picked" \/ "stock"/);assert.match(got.text,/Unscored \(baseline unscored, candidate unscored\)/);
  assert.doesNotMatch(got.text,/sensitive-app-log|private value|private thrown/);
  const inherited=run(t,`evaluate:({variant})=>({stock:false,missing:variant==='baseline'?false:undefined})`);
  assert.equal(inherited.code,1);assert.match(inherited.text,/Existing failure/);
  assert.match(inherited.text,/baseline failed, candidate unscored/);assert.match(inherited.text,/undetermined/);
});
test('holds on drift, setup or invalid selection before claiming a comparison',t=>{
  for (const invalid of [
    `maxCallsPerRun:{baseline:1,candidate:0}`, `maxCallsPerRun:{baseline:0,candidate:1}`,
    `baseline:undefined`, `evaluate:undefined`, `checks:[{id:'same'},{id:'same'}]`,
    `timeoutMs:-1`, `checksVersion:String(Math.random())`, `cases:[{id:'picked',input:{fresh:Math.random()}}]`,
  ]) {
    const got=run(t,invalid);assert.equal(got.code,2,got.text);assert.doesNotMatch(got.text,/Recovered:|Passed both:/);
  }
  const unknown=run(t,'',['--compare-baseline','--case=absent']);assert.equal(unknown.code,2);
  const inspect=run(t,'',['--compare-baseline','--inspect-values','--case=picked']);assert.equal(inspect.code,2);
});
test('deadline and rejected provider context cannot turn a missing score into a recovered check',t=>{
  const gone=run(t,`cases:[{id:'late',input:{}}],timeoutMs:35,baseline:(_,ctx)=>new Promise(resolve=>setTimeout(resolve,90)),candidate:()=>true,evaluate:()=>({stock:true,missing:true})`,['--compare-baseline']);
  assert.equal(gone.code,1,gone.text);assert.match(gone.text,/baseline unscored, candidate passed/);assert.doesNotMatch(gone.text,/Recovered:/);
  const blocked=run(t,`baseline:async (_,ctx)=>{await ctx.fetch('sensitive transport').catch(()=>{});return true;},evaluate:()=>({stock:true,missing:true})`);
  assert.equal(blocked.code,1,blocked.text);assert.doesNotMatch(blocked.text,/sensitive transport|Recovered:/);
});
test('an ordinary check still evaluates only its candidate',t=>{
  const got=run(t,`baseline:()=>{throw Error('never call this')},evaluate:({variant})=>({stock:variant==='candidate',missing:true})`,['--case=picked']);
  assert.equal(got.code,0,got.text);assert.doesNotMatch(got.text,/Baseline and candidate|never call/);
});

test('reports the direction of each repetition independently',t=>{
  const got=run(t,`baseline:(_,ctx)=>ctx.repetition!==0,candidate:(_,ctx)=>ctx.repetition!==1,
    evaluate:({output})=>({stock:output,missing:true})`);
  assert.equal(got.code,1,got.text);
  assert.match(got.text,/Recovered: "picked" \/ "stock" · trials 1\n/);
  assert.match(got.text,/New failure: "picked" \/ "stock" · trials 2\n/);
  assert.match(got.text,/Passed both: "picked" \/ "stock" · trials 3\n/);
});

test('a hard-blocked callback cannot turn into a fake pass, and a worker error does not verify',t=>{
  const stuck=run(t,`baseline:()=>{while(true){}},timeoutMs:20,evaluate:()=>({stock:true,missing:true})`,['--compare-baseline']);
  assert.equal(stuck.code,1,stuck.text);assert.match(stuck.text,/baseline unscored, candidate passed/);
  const crash=run(t,`candidate:()=>{process.exit(0)},evaluate:()=>({stock:true,missing:true})`,['--compare-baseline']);
  assert.equal(crash.code,2,crash.text);assert.doesNotMatch(crash.text,/Passed both:/);
  const fault=run(t,`candidate:()=>{process.exitCode=1;return true},evaluate:()=>({stock:true,missing:true})`,['--compare-baseline']);
  assert.equal(fault.code,2,fault.text);
});

test('binds declared imported criteria for both arms, including initialization and final trial',t=>{
  const cwd=mkdtempSync(join(tmpdir(),'paired-scorer-'));
  t.after(()=>rmSync(cwd,{force:true,recursive:true}));
  mkdirSync(join(cwd,'rules'));
  const adapter=join(cwd,'workflow.mjs'), rule=join(cwd,'rules','expected.mjs');
  const outside=join(cwd,'external');symlinkSync(tmpdir(),outside);
  for (const kind of ['stable','baseline write','candidate write','load write','missing','escape']) {
    writeFileSync(rule,'export const expected=5;');
    const change="writeFileSync(new URL('./rules/expected.mjs',import.meta.url),'export const expected=10;');";
    const declared=kind==='missing'?'./absent.mjs':kind==='escape'?'./external':'./rules/expected.mjs';
    writeFileSync(adapter,`import {writeFileSync} from 'node:fs';
import {expected} from './rules/expected.mjs';
${kind==='load write'?change:''}
export default {maxCallsPerRun:{baseline:0,candidate:0},evaluatorSourceFiles:[${JSON.stringify(declared)}],
  checks:[{id:'amount'}],cases:[{id:'receipt',input:10}],
  baseline:x=>{${kind==='baseline write'?change:''}return x},candidate:x=>{${kind==='candidate write'?change:''}return x},
  evaluate:({output})=>({amount:output===expected})};`);
    const child=spawnSync(process.execPath,[runner,'--offline-workflow','workflow.mjs','--compare-baseline','--case','receipt'],{cwd,encoding:'utf8'});
    assert.equal(child.status,kind==='stable'?1:2,kind+' '+child.stdout+child.stderr);
    if (kind==='stable') assert.match(child.stdout,/Existing failure: "receipt" \/ "amount"/);
    else {assert.match(child.stderr,/evaluator files/i);assert.doesNotMatch(child.stdout+child.stderr,/expected=10|Recovered:|Passed both:/);}
  }
});

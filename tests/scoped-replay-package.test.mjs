import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { appendFileSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = fileURLToPath(new URL('../', import.meta.url));
const pkg = version => join(root, 'packages', `tokensand-workflow-check-${version}.tgz`);
const latest = '0.1.27';
const sha = path => createHash('sha256').update(readFileSync(path)).digest('hex');

function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), 'tokensand-package-test-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = (name, text) => writeFileSync(join(dir, name), text);
  file('package.json', '{"scripts":{"test":"node --test"}}\n');
  file('observe.workflow.mjs', `import { invoke } from 'ai';
    invoke('moduleInit');
    export default { repetitions:1, timeoutMs:2000, maxCallsPerRun:{candidate:0},
      cases:[{id:'amount',input:null}], checks:[{id:'right',label:'Right amount'}],
      candidate:()=>{invoke('lookup');return 18},
      evaluate:()=>{invoke('scorer');return {right:false}} };`);
  mkdirSync(join(dir,'node_modules','ai'),{recursive:true});
  file('node_modules/ai/package.json', '{"name":"ai","type":"module","version":"7.0.183","exports":{".":"./index.mjs","./package.json":"./package.json"}}');
  file('node_modules/ai/index.mjs', `import {appendFileSync} from 'node:fs';
    let plugin;
    export function registerTelemetry(p){plugin=p;appendFileSync('registered.txt','registered\\n')}
    export function invoke(toolName){
      if (!plugin) return;
      const base={aiVersion:'7.0.183',callId:'group-'+toolName};
      const toolCall={toolCallId:'PrivateInvocationID',toolName};
      const msg=[{role:'assistant',content:[{type:'tool-call',...toolCall}]},
        {role:'tool',content:[{type:'tool-result',...toolCall,output:{type:'json',value:18}}]}];
      plugin.onStart(base);plugin.onLanguageModelCallStart({...base,messages:[]});
      plugin.onToolExecutionStart({...base,toolCall});
      plugin.onToolExecutionEnd({...base,toolCall,toolExecutionMs:2,toolOutput:{type:'tool-result',output:8}});
      plugin.onStepEnd({...base,response:{messages:msg}});
      plugin.onLanguageModelCallStart({...base,messages:msg});
      plugin.onEnd(base);
    }`);
  function run(command, args, extraEnv={}) {
    const proc = spawnSync(command,args,{cwd:dir,encoding:'utf8',timeout:20000,env:{
      PATH:process.env.PATH, HOME:dir,TMPDIR:dir,NO_COLOR:'1',TOKENSAND_TOOL_RESULT_FIELD:'.',
      npm_config_cache:join(dir,'npm-cache'), ...extraEnv,
    }});
    assert.ifError(proc.error);
    return {status:proc.status,text:proc.stdout+'\n'+proc.stderr};
  }
  function unpack(version) {
    const dest=join(dir,version);
    mkdirSync(dest);
    execFileSync('tar',['-xzf',pkg(version),'-C',dest]);
    return join(dest,'package');
  }
  return {dir,run,unpack};
}

function inspected(result) {
  assert.equal(result.status,1,result.text);
  assert.match(result.text,/AI SDK tool calls \(trial 1\): 1 recorded/);
  assert.match(result.text,/lookup — 1 returned, selected value changed in 1\/1 tool-to-model pairs/);
  assert.doesNotMatch(result.text,/moduleInit —|scorer —|PrivateInvocationID|modelResult":18/);
}

test('missing setup gives the same actionable next step in npm and the standalone download', t=>{
  const {dir,run,unpack}=fixture(t);
  const dist=unpack(latest);
  rmSync(join(dir,'observe.workflow.mjs'));
  const cli=join(dist,'cli.mjs'), standalone=join(dist,'standalone-check.mjs');
  const explicit=['--offline-workflow','./observe.workflow.mjs','--case=amount'];
  const results=[
    run('npm',['exec','--offline','--yes','--package',pkg(latest),'--','tokensand-check',...explicit]),
    run(process.execPath,[cli,'--case=amount']),
    run(process.execPath,[standalone,...explicit]),
  ];
  for (const result of results) {
    assert.equal(result.status,2,result.text);
    assert.match(result.text,/create observe\.workflow\.mjs/i);
    assert.match(result.text,/zero provider budget/);
    assert.doesNotMatch(result.text,/ERR_MODULE_NOT_FOUND|node:internal|\d+ passed/);
  }
  assert.equal(existsSync(join(dir,'tokensand-first.test.mjs')),false);
  assert.equal(existsSync(join(dir,'observe.workflow.mjs')),false);
});

test('npm exec, copied starter and direct release asset use the scoped inspector only on request', t=>{
  const {dir,run,unpack}=fixture(t);
  const dist=unpack(latest);
  const flags=['--case=amount','--inspect-tools'];
  const npm=(...args)=>run('npm',['exec','--offline','--yes','--package',pkg(latest),'--','tokensand-check',...args]);
  const regular=npm('--offline-workflow','./observe.workflow.mjs');
  assert.equal(regular.status,1,regular.text);
  assert.doesNotMatch(regular.text,/AI SDK tool calls|private JSONL/i);
  assert.equal(existsSync(join(dir,'registered.txt')),false);
  inspected(npm(...flags));
  const first=npm();
  assert.equal(first.status,1,first.text);
  assert.equal(sha(join(dir,'tokensand-first.test.mjs')),sha(join(dist,'standalone-check.mjs')));
  copyFileSync(join(dist,'standalone-check.mjs'),join(dir,'download.mjs'));
  for (const filename of ['tokensand-first.test.mjs','download.mjs']) {
    inspected(run(process.execPath,[filename,'--offline-workflow','./observe.workflow.mjs',...flags]));
  }
  assert.equal(readFileSync(join(dir,'registered.txt'),'utf8').trim().split('\n').length,3);
});

for (const [version, installedSha] of [
  ['0.1.19', '35d32d550d4b7d226b34b1bc3710f63624bf5bcd49bb65cd6a3a4f6cc08e4ac6'],
  ['0.1.24', '35d32d550d4b7d226b34b1bc3710f63624bf5bcd49bb65cd6a3a4f6cc08e4ac6'],
  ['0.1.25', 'e80168f21af0b37b274348f27fb6e9a0e50a7287560284f7db813ae2b59151f6'],
  ['0.1.26', '2a8e9f35bc7a988417a88d2b10d0ae6bcc565d6db13df75c3d0227dfb63f47a4'],
]) {
  test('upgrades the unmodified '+version+' runner and protects local edits',t=>{
    const {dir,run,unpack}=fixture(t);
    const old=unpack(version), next=unpack(latest);
    const install=(where)=>run(process.execPath,[join(where,'cli.mjs'),'--install-test']);
    assert.equal(install(old).status,1);
    const generated=join(dir,'tokensand-check.mjs');
    assert.equal(sha(generated),installedSha);
    const original=readFileSync(generated);
    const manifest=readFileSync(join(dir,'package.json'));
    appendFileSync(generated,'\n// local customization\n');
    const changed=readFileSync(generated);
    assert.equal(install(next).status,2);
    assert.deepEqual(readFileSync(generated),changed);
    assert.deepEqual(readFileSync(join(dir,'package.json')),manifest);
    writeFileSync(generated,original);
    const result=install(next);
    assert.equal(result.status,1,result.text);
    assert.doesNotMatch(result.text,/private JSONL/);
    assert.equal(existsSync(join(dir,'registered.txt')),false);
    assert.deepEqual(readFileSync(join(dir,'package.json')),manifest);
    assert.notDeepEqual(readFileSync(generated),original);
    rmSync(old,{recursive:true});rmSync(next,{recursive:true});
    const guarded=run(process.execPath,[generated,'--case=amount','--inspect-tools'],{GITHUB_ACTIONS:'true',GITHUB_EVENT_NAME:'pull_request'});
    assert.equal(guarded.status,2);assert.match(guarded.text,/no cases evaluated/);
    assert.equal(existsSync(join(dir,'registered.txt')),false);
    inspected(run(process.execPath,[generated,'--case=amount','--inspect-tools']));
  });
}

test('packaged checker and preload exactly match the Agent Pack; unaffected release tools keep their bytes',t=>{
  const {unpack}=fixture(t);
  const old=unpack('0.1.26'), next=unpack(latest);
  const scripts=join(root,'plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts');
  assert.equal(sha(join(next,'agent-check.mjs')),sha(join(scripts,'check_workflow.mjs')));
  assert.equal(sha(join(next,'trace_ai_sdk_7.mjs')),sha(join(scripts,'trace_ai_sdk_7.mjs')));
  const manifest=JSON.parse(readFileSync(join(old,'package.json')));
  const verifier='recheck-release-v1.mjs';
  assert.equal(readFileSync(join(next,verifier),'utf8'),readFileSync(join(old,verifier),'utf8')
    .replace(sha(join(old,'agent-check.mjs')),sha(join(next,'agent-check.mjs'))));
  assert.deepEqual(JSON.parse(readFileSync(join(next,'package.json'))).files,manifest.files);
  assert.equal(sha(join(next,'watch-workflow.mjs')),sha(join(root,'packages','watch-workflow.mjs')));
  for(const name of manifest.files.filter(x=>!['watch-workflow.mjs','install-test.mjs','agent-check.mjs','standalone-check.mjs',verifier].includes(x)))
    assert.equal(sha(join(old,name)),sha(join(next,name)),name);
});

test('recipients replay the frozen checks and reject a modified packaged checker',t=>{
  const {dir,unpack}=fixture(t);
  const dist=unpack(latest);
  const repo=join(dir,'source');
  const note=join(dir,'release.json');
  execFileSync('git',['init','-q',repo]);
  const git=(...args)=>execFileSync('git',args,{cwd:repo,encoding:'utf8'}).trim();
  function revision(working) {
    writeFileSync(join(repo,'observe.workflow.mjs'),`export default {
      id:'order-support',timeoutMs:3000,maxCallsPerRun:{baseline:0,candidate:0},cases:[{id:'amount',input:8}],checks:[{id:'right'}],
      candidate:n=>n+${working?0:10},evaluate:({input,output})=>({right:input===output})
    };`);
    git('add','observe.workflow.mjs');
    git('-c','user.name=Giovanni Amenta','-c','user.email=giovanni@creatorscorner.co',
      '-c','commit.gpgsign=false','commit','-qm',working?'Record original amount':'Change converted amount');
    return git('rev-parse','HEAD');
  }
  const referenceRevision=revision(true), candidateRevision=revision(false);
  writeFileSync(note,JSON.stringify({kind:'tokensand-release-note',schemaVersion:1,selection:'all-recorded-cases',
    referenceRevision,candidateRevision,cases:[{id:'amount',verdict:'regressed',
      reference:{passed:3,total:3},candidate:{passed:0,total:3}}]}));
  function replay(selection,expected) {
    const result=spawnSync(process.execPath,[join(dist,'cli.mjs'),'--recheck-release',note,...selection,'--run'],
      {cwd:repo,encoding:'utf8',timeout:30000,env:{PATH:process.env.PATH,HOME:dir,TMPDIR:dir}});
    assert.ifError(result.error);
    assert.equal(result.status,expected,result.stdout+result.stderr);
    return JSON.parse(result.stdout);
  }
  assert.equal(replay(['--all-cases'],100).verdict,'regressed');
  assert.equal(replay(['--case','amount'],100).verdict,'regressed');
  appendFileSync(join(dist,'agent-check.mjs'),'\n// changed by a local install\n');
  const held=replay(['--case','amount'],2);
  assert.equal(held.verdict,'unknown');
  assert.match(held.reason,/matching public workflow checker/);
});

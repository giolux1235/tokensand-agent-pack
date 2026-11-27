import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

const helper = new URL("../plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/trace_ai_sdk_7.mjs", import.meta.url);
const imports = `import assert from "node:assert/strict";
import {createTrialToolInspector} from ${JSON.stringify(helper.href + "?trial")};
const capture = await createTrialToolInspector();`;

function exercise(t, source, { sdk = "fake", selector, preload = false } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "tokensand-trial-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const scratch = join(dir, "temp");
  mkdirSync(scratch);
  if (sdk === "fake") {
    const pkg = join(dir, "node_modules", "ai");
    mkdirSync(pkg, { recursive: true });
    writeFileSync(join(pkg, "package.json"), JSON.stringify({
      name: "ai", version: "7.0.183", type: "module", exports: { ".": "./index.mjs", "./package.json": "./package.json" },
    }));
    writeFileSync(join(pkg, "index.mjs"), "export function registerTelemetry(plugin) { assertSingle(); globalThis.sdk = plugin; } function assertSingle() { if(globalThis.sdk) throw Error('Duplicate registration'); }");
  } else if (sdk) {
    symlinkSync(resolve(sdk, "node_modules"), join(dir, "node_modules"), "dir");
  }
  writeFileSync(join(dir, "example.mjs"), imports + "\n" + source);
  const env = { PATH: process.env.PATH, TMPDIR: scratch, TEMP: scratch, TMP: scratch };
  if (selector !== undefined) env.TOKENSAND_TOOL_RESULT_FIELD = selector;
  const args = [...(preload ? ["--import", helper.href] : []), "example.mjs"];
  const run = spawnSync(process.execPath, args, { cwd: dir, env, encoding: "utf8", timeout: 10000 });
  assert.equal(run.status, 0, run.stderr);
  const paths = [...run.stderr.matchAll(/private JSONL (".*")/g)].map(match => JSON.parse(match[1]));
  assert(paths.length <= 1);
  const file = paths.length ? readFileSync(paths[0], "utf8") : "";
  return { summary: JSON.parse(run.stdout), stderr: run.stderr, rows: file.trim().split("\n").filter(Boolean).map(JSON.parse) };
}

const fakeEvents = `
const wait = () => new Promise(resolve => setImmediate(resolve));
const sdk = globalThis.sdk;
function start(callId) {
  const shared = {aiVersion:"7.0.183", callId};
  sdk.onStart(shared);
  sdk.onLanguageModelCallStart?.({...shared, messages:[]});
  return shared;
}
function done(shared, toolName, value, modelValue, error = false) {
  const toolCallId = "private-call-id";
  const event = {...shared,toolCall:{toolCallId,toolName}};
  sdk.onToolExecutionStart(event);
  sdk.onToolExecutionEnd({...event,toolOutput:error?{type:"tool-error",error:"SENSITIVE_ERROR"}:{type:"tool-result",output:value}});
  const messages = [
    {role:"assistant",content:[{type:"tool-call",toolCallId,toolName}]},
    {role:"tool",content:[{type:"tool-result",toolCallId,toolName,output:{type:"json",value:modelValue}}]}
  ];
  sdk.onStepEnd?.({...shared,response:{messages}});
  sdk.onLanguageModelCallStart?.({...shared,messages});
  sdk.onEnd(shared);
}`;

test("scope starts at the candidate, survives overlapping generations, and expires before the scorer", t => {
  for (const preload of [false, true]) {
    const {summary,rows,stderr} = exercise(t, fakeEvents + `
      const a=capture.startTrial(1), b=capture.startTrial(2);
      let releaseA; const pending=new Promise(resolve => { releaseA=resolve; });
      const aRun=a.run(async()=>{
        const first=start("generation-a"), further=start("generation-a-more");
        await pending;
        done(first,"stock",8,18);
        done(further,"stock",22,22);
        const unfinished=start("generation-a-late");
        setImmediate(() => { done(unfinished,"detached",7201,7202); done(start("generation-late-new"),"background",7301,7302); });
      });
      await b.run(async()=>{ await wait(); done(start("generation-b"),"shipping",2,2); done(start("generation-b-error"),"supplier",0,0,true); });
      releaseA(); await aRun;
      // Simulate a slow scorer before it calls close(), as the checker may do.
      done(start("outside"),"scorer",7401,7402); await wait();
      const results=[a.close(),b.close()]; assert.deepEqual(a.close(),results[0]);
      console.log(JSON.stringify(results));`, {selector:".",preload});
    assert.deepEqual(summary[0],{available:true,comparison:true,observed:2,omitted:1,
      tools:[{tool:"stock",completed:2,errors:0,changed:1,equal:1,unavailable:0}]});
    assert.deepEqual(summary[1],{available:true,comparison:true,observed:2,omitted:0,tools:[
      {tool:"shipping",completed:1,errors:0,changed:0,equal:1,unavailable:0},
      {tool:"supplier",completed:1,errors:1,changed:0,equal:0,unavailable:0},
    ]});
    assert.deepEqual(rows.map(r => [r.trial,r.tool]),[[2,"shipping"],[2,"supplier"],[1,"stock"],[1,"stock"]]);
    assert.doesNotMatch(JSON.stringify(rows) + stderr,/private-call-id|SENSITIVE_ERROR|720[12]|730[12]|740[12]/);
  }
});

test("timing-only inspection reports completed calls without deriving value differences", t => {
  const {summary,rows}=exercise(t,fakeEvents+`
    const trial=capture.startTrial(1);
    const result = await trial.run(()=>{
      done(start("a"),"stock",6,7);
      queueMicrotask(()=>done(start("sync-detached"),"after",1291,1292));
      return { ok:true };
    });
    assert.deepEqual(result,{ok:true});
    console.log(JSON.stringify(trial.close()));`);
  assert.equal(summary.comparison,false);
  assert.deepEqual(summary.tools,[{tool:"stock",completed:1,errors:0,changed:0,equal:0,unavailable:0}]);
  assert(!Object.hasOwn(rows[0],"result") && !Object.hasOwn(rows[0],"modelResult"));
});

test("an absent SDK leaves an ordinary project quiet and preserves candidate rejection", t => {
  const {summary,stderr,rows}=exercise(t,`
    const trial=capture.startTrial(1);
    const sentinel=Error("original app error");
    await assert.rejects(trial.run(()=>{throw sentinel}),e=>e===sentinel);
    console.log(JSON.stringify(trial.close()));`,{sdk:null});
  assert.equal(stderr,"");
  assert.deepEqual(rows,[]);
  assert.deepEqual(summary,{available:false,comparison:false,observed:0,omitted:0,tools:[]});
});

const real = process.env.TOKENSAND_TEST_AI_SDK7_DIR;
test("native AI SDK 7 propagates two concurrent candidate scopes and honors application redaction", {skip:!real}, t => {
  const {summary,rows}=exercise(t,`
    const {generateText,stepCountIs,tool}=await import("ai");
    const {MockLanguageModelV4}=await import("ai/test");
    const {z}=await import("zod");
    async function replay(value,privateOutput) {
      let calls=0;
      const model=new MockLanguageModelV4({doGenerate:async()=>({
        ...(!calls++?{content:[{type:"tool-call",toolCallId:"reused",toolName:"count",input:"{}"}],finishReason:{unified:"tool-calls",raw:"tool_calls"}}
        :{content:[{type:"text",text:"ok"}],finishReason:{unified:"stop",raw:"stop"}}),
        usage:{inputTokens:{},outputTokens:{}},warnings:[]
      })});
      await generateText({model,prompt:"local test",stopWhen:stepCountIs(2),maxRetries:0,
        telemetry:privateOutput?{recordOutputs:false}:undefined,
        tools:{count:tool({inputSchema:z.object({}),execute:async()=>value,toModelOutput:({output})=>({type:"json",value:output+1})})}});
    }
    const a=capture.startTrial(1),b=capture.startTrial(2);
    await Promise.all([a.run(()=>replay(6,false)),b.run(()=>replay(9,true))]);
    console.log(JSON.stringify([a.close(),b.close()]));`,{sdk:real,selector:"."});
  assert.equal(summary[0].tools[0].changed,1);
  assert.equal(summary[1].tools[0].unavailable,1);
  assert.deepEqual(rows.filter(r=>r.trial===1).map(r=>[r.result,r.modelResult]),[[6,7]]);
  assert(rows.some(r=>r.trial===2 && !Object.hasOwn(r,"result") && !Object.hasOwn(r,"modelResult")));
});

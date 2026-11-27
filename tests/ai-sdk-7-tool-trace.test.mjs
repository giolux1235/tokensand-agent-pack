import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createRequire } from "node:module";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const preload = fileURLToPath(new URL("../plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/trace_ai_sdk_7.mjs", import.meta.url));
const fake = 'export function registerTelemetry(plugin) { (globalThis.plugins ??= []).push(plugin); }';
const setup = `
import assert from "node:assert/strict";
const [plugin] = globalThis.plugins;
const op = (callId, extra={}) => ({ aiVersion:"7.0.183", callId, ...extra });
const event = (c,id,name,extra={}) => ({...op(c),toolCall:{toolCallId:id,toolName:name,input:"PRIVATE_INPUT"}, messages:["PRIVATE_PROMPT"],...extra});
const begin = (c,id,name,extra) => plugin.onToolExecutionStart(event(c,id,name,extra));
const end = (c,id,name,value,extra={}) => plugin.onToolExecutionEnd(event(c,id,name,{toolExecutionMs:3.4,toolOutput:{type:"tool-result",output:value},...extra}));
const run = (id, output, extra={}) => { plugin.onStart(op(id)); begin(id,"PRIVATE_ID","readStock"); end(id,"PRIVATE_ID","readStock",output,extra); plugin.onEnd(op(id)); };
`;

function consumer(t, { version = "7.0.183", sdk = fake, dependencies } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "tokensand-ai7-test-"));
  t.after(() => rmSync(dir, { recursive:true, force:true }));
  if (dependencies) symlinkSync(dependencies, join(dir,"node_modules"), "dir");
  else if (version) {
    const pkg = join(dir,"node_modules","ai");
    mkdirSync(pkg,{recursive:true});
    writeFileSync(join(pkg,"package.json"),JSON.stringify({name:"ai",version,type:"module",exports:{".":"./index.mjs","./package.json":"./package.json"}}));
    writeFileSync(join(pkg,"index.mjs"),sdk);
  }
  const temp = join(dir,"tmp");
  mkdirSync(temp);
  function execute(script, options={}) {
    const app = join(dir,"app.mjs");
    writeFileSync(app,script);
    const args = [...(options.preloads ?? [preload]).flatMap(file => ["--import",file]),app];
    const result = spawnSync(process.execPath,args,{cwd:dir,env:{PATH:process.env.PATH,HOME:dir,TMPDIR:temp,...options.env},encoding:"utf8",timeout:15000});
    assert.equal(result.status,options.exit ?? 0,result.stderr);
    const matches = [...result.stderr.matchAll(/private JSONL (".*")/g)];
    assert(matches.length <= 1);
    const path = matches[0] ? JSON.parse(matches[0][1]) : null;
    if (path) {
      assert.equal(statSync(path).mode & 0o777,0o600);
      assert.equal(statSync(join(path,"..")).mode & 0o777,0o700);
    }
    const file = path ? readFileSync(path,"utf8") : "";
    assert.doesNotMatch(file + result.stderr,/PRIVATE_(?:PROMPT|INPUT|ERROR|ID|VALUE)|sk-never-print|SECRET_SELECTOR/);
    return { ...result, dir, file, rows:file.trim().split("\n").filter(Boolean).map(line => JSON.parse(line)), temp };
  }
  return execute;
}

test("overlapping generations, reversed finishes and reused provider IDs stay local to each generation",t=>{
  const exec = consumer(t);
  const r = exec(setup+`
    for (const c of ["PRIVATE_ID_A","PRIVATE_ID_B"]) {
      plugin.onStart(op(c));begin(c,"PRIVATE_ID_1","stock");begin(c,"PRIVATE_ID_2","shipping");
    }
    for (const c of ["PRIVATE_ID_B","PRIVATE_ID_A"]) {
      end(c,"PRIVATE_ID_2","shipping","PRIVATE_VALUE");end(c,"PRIVATE_ID_1","stock","PRIVATE_VALUE");plugin.onEnd(op(c));
    }
    console.log("app-output");process.exitCode=19;
  `,{exit:19});
  assert.equal(r.stdout,"app-output\n");
  assert.equal(r.rows.length,4);
  for(const group of [1,2]) assert.deepEqual(r.rows.filter(row=>row.generation===group),[
    {generation:group,startOrder:1,finishOrder:2,tool:"stock",status:"returned",durationMs:3.4},
    {generation:group,startOrder:2,finishOrder:1,tool:"shipping",status:"returned",durationMs:3.4},
  ]);
});

test("explicit selection traverses only bounded scalar own data and honors redaction",t=>{
  const exec=consumer(t);
  const r=exec(setup+`
    run("a",{finite:{units:8},payload:"PRIVATE_VALUE"});
    run("b",{finite:{units:0}});run("c",{finite:{units:null}});
    run("d",Object.create({finite:{units:"PRIVATE_VALUE"}}));
    run("e",{get finite(){throw Error("PRIVATE_ERROR")}});
    run("f",{finite:new Proxy({},{getOwnPropertyDescriptor(){throw Error("PRIVATE_ERROR")}})});
    run("g",{finite:{units:{nested:"PRIVATE_VALUE"}}});
    run("h",{finite:{units:"x".repeat(200)}});run("i",{finite:{units:"sk-never-print"}});
    run("j",{finite:{units:"PRIVATE_VALUE"}},{recordOutputs:false});
    plugin.onStart(op("k",{recordOutputs:false}));begin("k","id","lookup");end("k","id","lookup",{finite:{units:"PRIVATE_VALUE"}});plugin.onEnd(op("k"));
    run("l",{finite:{units:"PRIVATE_VALUE"}},{toolOutput:{type:"tool-error",error:"PRIVATE_ERROR"}});
  `,{env:{TOKENSAND_TOOL_RESULT_FIELD:"finite.units"}});
  assert.deepEqual(r.rows.filter(row=>Object.hasOwn(row,"result")).map(row=>row.result),[8,0,null]);
  assert.equal(r.rows.at(-1).status,"error");
  assert.doesNotMatch(r.stderr,/units|finite|returned/);
});

test("concurrent imports register the global callback only once", t => {
  const path = pathToFileURL(preload).href;
  const r = consumer(t)(`await Promise.all([import(${JSON.stringify(path+"?one")}), import(${JSON.stringify(path+"?two")})]); console.log(globalThis.plugins.length);`,
    { preloads: [] });
  assert.equal(r.stdout, "1\n");
});

test("credential and authentication selectors never copy the selected scalar", t => {
  const exec = consumer(t);
  for (const field of ["refreshToken", "access_token", "sessionToken", "auth.oneTimeCode", "otp", "private_key"]) {
    const r = exec(setup + 'run("a",{refreshToken:"PRIVATE_VALUE",access_token:4,sessionToken:true,auth:{oneTimeCode:12345},otp:42,private_key:"x"});',
      { env: { TOKENSAND_TOOL_RESULT_FIELD: field } });
    assert(!Object.hasOwn(r.rows[0], "result"));
    assert.match(r.stderr, /Invalid result field/);
  }
});

test("missing IDs, duplicate starts or ends, unsafe labels, collisions and repeated loading produce no false join",t=>{
  const exec=consumer(t);
  const r=exec(setup+`
    assert.equal(globalThis.plugins.length,1);
    plugin.onStart(op("group"));
    end("group","orphan","a",1);begin("group","orphan","a");end("group","orphan","a",2);
    begin("group","dupe","a");begin("group","dupe","a");end("group","dupe","a",2);
    begin("group",null,"a");end("group",null,"a",2);
    begin("group","twice","a");end("group","twice","a",1);end("group","twice","a",2);
    begin("group","label","a");end("group","label","b",2);
    begin("group","sk-never-print","sk-never-print");end("group","sk-never-print","sk-never-print",24);
    begin("group","valid","lookup");end("group","valid","lookup",8);
    plugin.onEnd(op("group"));
    plugin.onStart(op("early"));plugin.onStart(op("early"));plugin.onEnd(op("early"));
    run("early",999);
    plugin.onStart(op("collision"));begin("collision","same","a");plugin.onStart(op("collision"));
    end("collision","same","a",1);plugin.onEnd(op("collision"));
  `,{env:{TOKENSAND_TOOL_RESULT_FIELD:"."},preloads:[preload,new URL("?second", pathToFileURL(preload)).href]});
  assert.deepEqual(r.rows.map(row=>[row.tool,row.result]),[["lookup",8]]);
  assert.match(r.stderr,/ambiguous/);
});

test("model values require a matching converted result from the same fresh step and a unique adjacent call", t => {
  const exec = consumer(t);
  const r = exec(setup + `
    const parts=(id,value)=>[{role:"assistant",content:[{type:"tool-call",toolCallId:id,toolName:"lookup"}]},
      {role:"tool",content:[{type:"tool-result",toolCallId:id,toolName:"lookup",output:{type:"json",value}}]}];
    for(const mode of ["valid","history","duplicate","wrong-generation","inputs-off","no-step","changed","later","conflicting-step"]){
      const id="PRIVATE_ID";
      plugin.onStart(op(mode));plugin.onLanguageModelCallStart(op(mode,{messages:[]}));
      begin(mode,id,"lookup");end(mode,id,"lookup",2);
      if(mode==="conflicting-step")plugin.onStepEnd(op(mode,{response:{messages:parts(id,5)}}));
      if(mode!=="no-step")plugin.onStepEnd(op(mode,{response:{messages:parts(id,8)}}));
      const list=parts(mode==="wrong-generation"?"another-id":id,mode==="changed"?"PRIVATE_VALUE":8);
      if(mode==="history")list.push({role:"user",content:"PRIVATE_PROMPT"});
      if(mode==="duplicate")list[1].content.push(list[1].content[0]);
      if(mode==="later")plugin.onLanguageModelCallStart(op(mode,{messages:[]}));
      plugin.onLanguageModelCallStart(op(mode,{messages:list,recordInputs:mode!=="inputs-off"}));
      plugin.onEnd(op(mode));
    }
  `, { env: { TOKENSAND_TOOL_RESULT_FIELD:"." } });
  assert.equal(r.rows.length,9);
  assert.deepEqual(r.rows.filter(row=>Object.hasOwn(row,"modelResult")).map(row=>row.modelResult),[8]);
  assert(r.rows.every(row=>row.result===2));
});

test("bounds records and only reports a path when there are matched calls",t=>{
  const exec=consumer(t);
  const empty=exec(setup+'console.log("ordinary app");');
  assert.match(empty.stderr,/0 matched tool calls/);
  assert.deepEqual(readdirSync(empty.temp),[]);
  const capped=exec(setup+'for(let i=0;i<500;i++)run("group-"+i, true);',{env:{TOKENSAND_TOOL_RESULT_FIELD:"__proto__.SECRET_SELECTOR"}});
  assert.equal(capped.rows.length,256);
  assert(capped.rows.every(row=>!Object.hasOwn(row,"result")));
  assert.match(capped.stderr,/Invalid result field/);
});

test("unsupported, absent or explicitly disabled SDK leaves the unmodified application alone",t=>{
  for(const version of ["6.0.338",null]){
    const r=consumer(t,{version,sdk:'throw Error("SDK must not load")'})('console.log("existing app");');
    assert.equal(r.stdout,"existing app\n");
    assert.match(r.stderr,/Vercel AI SDK 7.*without --import/);
    assert.deepEqual(readdirSync(r.temp),[]);
  }
  const off=consumer(t)('console.log(globalThis.plugins)',{env:{TOKENSAND_TOOL_TRACE:"0"}});
  assert.equal(off.stdout,"undefined\n");
  assert.equal(off.stderr,"");
});

// A checkout of the existing consumer's pinned ai@7 + zod suffices. No live model is needed.
const nativeDir=process.env.TOKENSAND_TEST_AI_SDK7_DIR;
test("native v7 registers alongside app callbacks, obeys per-call privacy and local overrides", {skip: !nativeDir},t=>{
  const deps=createRequire(resolve(nativeDir,"package.json"));
  const localModules=resolve(nativeDir,"node_modules");
  assert(existsSync(localModules));
  assert.match(JSON.parse(readFileSync(deps.resolve("ai/package.json"),"utf8")).version,/^7\./);
  const exec=consumer(t,{dependencies:localModules});
  const source=`
    import {generateText,registerTelemetry,stepCountIs,tool} from "ai";
    import {MockLanguageModelV4} from "ai/test";import {z} from "zod";
    import assert from "node:assert/strict";
    const counts={global:0,local:0};const app={onToolExecutionEnd(){counts.global++}};registerTelemetry(app);
    const mode=process.env.MODE;
    const telemetry=mode==="redact"?{recordOutputs:false,recordInputs:false}:mode==="inputs-off"?{recordInputs:false}:mode==="local"?{integrations:[{onToolExecutionEnd(){counts.local++}}]}:mode==="off"?{isEnabled:false}:undefined;
    const usage={inputTokens:{total:undefined},outputTokens:{total:undefined}};
    const run=async value=> {
      let n=0;
      const model=new MockLanguageModelV4({doGenerate:async({prompt})=>{
        if(n && (mode==="transform" || mode==="to-text")){
          const results=prompt.filter(m=>m.role==="tool").flatMap(m=>m.content);
          assert.equal(results.length,1);assert.deepEqual(results[0].output,mode==="to-text"?{type:"text",value:"converted"}:{type:"json",value:{stats:{units:value+10}}});
        }
        return {...(!n++?{content:[{type:"tool-call",toolName:"lookup",toolCallId:"PRIVATE_ID",input:'{}'}],finishReason:{unified:"tool-calls",raw:"tool_calls"}}:{content:[{type:"text",text:"ok"}],finishReason:{unified:"stop",raw:"stop"}}),usage,warnings:[]};}});
      const r=await generateText({model, prompt:"PRIVATE_PROMPT",stopWhen:stepCountIs(2),maxRetries:0,telemetry,
        tools:{lookup:tool({inputSchema:z.object({}),execute:async()=>({stats:{units:value},payload:"PRIVATE_VALUE"}),
          ...(mode==="transform"?{toModelOutput:({output})=>({type:"json",value:{stats:{units:output.stats.units+10}}})}:mode==="to-text"?{toModelOutput:()=>({type:"text",value:"converted"})}:{})})}});
      assert.equal(r.text,"ok");
    };
    await Promise.all([run(8),run(2)]);console.log(JSON.stringify(counts));
  `;
  for(const mode of ["normal","transform","to-text","inputs-off","redact","local","off"]) {
    const env={MODE:mode,TOKENSAND_TOOL_RESULT_FIELD:mode==="to-text"?".":"stats.units"};
    const control=exec(source,{preloads:[],env});
    const actual=exec(source,{env});
    assert.equal(actual.stdout,control.stdout);
    if (mode==="normal" || mode==="transform") {
      assert.deepEqual(actual.rows.map(r=>r.result).sort((a,b)=>a-b),[2,8]);
      assert.equal(new Set(actual.rows.map(r=>r.generation)).size,2);
      assert(actual.rows.every(r=>r.modelResult === r.result + (mode==="transform" ? 10 : 0)));
    } else if (mode==="to-text") {
      assert.equal(actual.rows.length,2);assert(actual.rows.every(r=>!Object.hasOwn(r,"result") && r.modelResult==="converted"));
    } else if (mode==="inputs-off") {
      assert.deepEqual(actual.rows.map(r=>r.result).sort((a,b)=>a-b),[2,8]);
      assert(actual.rows.every(r=>!Object.hasOwn(r,"modelResult")));
    } else if (mode==="redact") {
      assert.equal(actual.rows.length,2);assert(actual.rows.every(r=>!Object.hasOwn(r,"result") && !Object.hasOwn(r,"modelResult")));
    } else {
      assert.deepEqual(actual.rows,[]);assert.match(actual.stderr,/0 matched tool calls/);
    }
  }
});

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const checker = fileURLToPath(new URL("../plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/check_workflow.mjs", import.meta.url));
const dependencies = process.env.TOKENSAND_TEST_AI_SDK7_DIR;
function consumer(t) {
  const dir = mkdtempSync(join(tmpdir(), "tokensand-case-inspect-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  function run(args, { program = checker, field } = {}) {
    return spawnSync(process.execPath, [program, "--offline-workflow", "./workflow.mjs", ...args], {
      cwd:dir, encoding:"utf8", timeout:15000, env:{
        PATH:process.env.PATH, HOME:dir, TMPDIR:dir, TEMP:dir, TMP:dir,
        ...(field === undefined ? {} : {TOKENSAND_TOOL_RESULT_FIELD:field})
      }
    });
  }
  return {dir,run};
}

test("a single-file copy can inspect wrapper calls and rejects an unknown selected case", t => {
  const {dir,run} = consumer(t);
  const program = join(dir,"standalone.mjs");
  copyFileSync(checker,program);
  writeFileSync(join(dir,"workflow.mjs"),`
    export default {maxCallsPerRun:{candidate:0}, cases:[{id:"one",input:1}], checks:[{id:"expected"}],
      candidate:(n,ctx)=>ctx.tool("work",()=>n),evaluate:({output})=>({expected:output===1})};`);
  const valid=run(["--case=one","--inspect-tools"],{program});
  assert.equal(valid.status,0,valid.stderr);
  assert.equal((valid.stdout.match(/1 of 1 ctx.tool callbacks/g)||[]).length,3);
  assert.doesNotMatch(valid.stdout+valid.stderr,/AI SDK tool calls|private JSONL/);
  assert.equal(run(["--case=absent","--inspect-tools"],{program}).status,2);
});

test("the printed replay attributes SDK calls to the trial and keeps the scored verdict independent", {skip:!dependencies}, t => {
  const {dir,run} = consumer(t);
  symlinkSync(resolve(dependencies,"node_modules"),join(dir,"node_modules"),"dir");
  writeFileSync(join(dir,"workflow.mjs"),`
    import {generateText,tool,stepCountIs} from "ai";
    import {MockLanguageModelV4} from "ai/test";
    import {z} from "zod";
    async function currency() {
      let turn=0;
      const model=new MockLanguageModelV4({doGenerate:async({prompt})=>{
        const first=turn++===0;
        const selected=first?undefined:prompt.find(m=>m.role==="tool").content[0].output.value;
        return {content:first?[{type:"tool-call",toolCallId:"private-provider-id",toolName:"invoiceBalance",input:"{}"}]:[{type:"text",text:String(selected)}],
          finishReason:{unified:first?"tool-calls":"stop",raw:first?"tool_calls":"stop"},usage:{inputTokens:{},outputTokens:{}},warnings:[]};
      }});
      const out=await generateText({model,prompt:"private synthetic prompt",maxRetries:0,stopWhen:stepCountIs(2),
        tools:{invoiceBalance:tool({inputSchema:z.object({}),execute:async()=>1234500,toModelOutput:({output})=>({type:"json",value:output/100})})}});
      return Number(out.text);
    }
    export default {maxCallsPerRun:{candidate:0},cases:[{id:"jpy",input:1234500},{id:"usd",input:12345}],
      checks:[{id:"amount",label:"Requested amount"}],candidate:()=>currency(),evaluate:({output,input})=>({amount:output===input})};`);
  const initial=run([]);
  assert.equal(initial.status,1,initial.stdout);
  assert.match(initial.stdout,/Replay this case: .*--case=jpy --inspect-tools/);
  assert.doesNotMatch(initial.stdout+initial.stderr,/AI SDK tool calls|private JSONL/);
  assert.equal(readdirSync(dir).filter(x=>x.startsWith("tokensand-tools-")).length,0);

  for(const [id,status] of [["jpy",1],["usd",0]]){
    const result=run(["--case="+id,"--inspect-tools"],{field:"."});
    assert.equal(result.status,status,result.stdout+result.stderr);
    assert.equal((result.stdout.match(/invoiceBalance — 1 returned, selected value changed in 1\/1 tool-to-model pairs/g)||[]).length,3);
    assert.doesNotMatch(result.stdout+result.stderr,/0 of 0|1234500|private-provider-id|private synthetic prompt/);
    const match=result.stderr.match(/private JSONL (".*")/);
    assert.ok(match);
    const rows=readFileSync(JSON.parse(match[1]),"utf8").trim().split("\n").map(JSON.parse);
    assert.deepEqual(rows.map(row=>[row.trial,row.result,row.modelResult]),[[1,1234500,12345],[2,1234500,12345],[3,1234500,12345]]);
  }
});

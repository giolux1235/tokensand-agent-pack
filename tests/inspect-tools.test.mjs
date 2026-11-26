import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

function consumer(t, candidate, timeoutMs = 2000) {
  const dir = mkdtempSync(join(tmpdir(), "tokensand-tool-replay-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const name = "tokensand-workflow-check-0.1.13.tgz";
  const archive = ["./distribution/", "../packages/"]
    .map(base => fileURLToPath(new URL(base + name, import.meta.url))).find(existsSync);
  execFileSync("tar", ["xzf", archive, "-C", dir]);
  writeFileSync(join(dir, "observe.workflow.mjs"), `
    import {setTimeout as wait} from "node:timers/promises";
    export default {maxCallsPerRun:{candidate:0}, timeoutMs:${timeoutMs},
      cases:[{id:"review",input:"private-input-fixture"}],
      checks:[{id:"correct"}], evaluate:({output})=>({correct:output===true}),
      async candidate(input,ctx) { ${candidate} }};
  `);
  return (...args) => {
    const result = spawnSync(process.execPath, ["package/cli.mjs","--offline-workflow","./observe.workflow.mjs",...args],
      {cwd:dir,encoding:"utf8",timeout:10000});
    assert.ifError(result.error);
    return {code:result.status, output:result.stdout+result.stderr};
  };
}

test("shows a rejected attempt and a successful retry without their arguments, outputs or exception contents", t => {
  const run = consumer(t, `
    try { await ctx.tool("ticket-create",()=>{throw Error("private-provider-failure");}); } catch {}
    const v=await ctx.tool("ticket-retry",()=>input);
    await ctx.tool("tobs_do_not_echo",()=>7);
    return v===input;
  `);
  const plain = run("--case","review");
  assert.equal(plain.code,0);
  assert.doesNotMatch(plain.output,/ticket-create|ticket-retry|Local tool calls/);
  const focused = run("--case","review","--inspect-tools");
  assert.equal(focused.code,0);
  for (const expected of [/#1 ticket-create — rejected, \d+ ms/g,/#2 ticket-retry — returned, \d+ ms/g,/#3 \[unnamed\] — returned/g]) {
    assert.equal((focused.output.match(expected) ?? []).length,3);
  }
  assert.doesNotMatch(focused.output,/private-input-fixture|private-provider-failure|tobs_do_not_echo/);
  assert.equal(run("--inspect-tools").code,2);
});

test("snapshots overlapping tools at the deadline once per trial", t => {
  const run = consumer(t, `
    await Promise.all([
      ctx.step("read_queue",()=>ctx.tool("read-fast",()=>wait(4,null,{signal:ctx.signal}))),
      ctx.tool("read-delayed",()=>wait(180,null,{signal:ctx.signal}))
    ]);
    return true;
  `,80);
  const result=run("--case","review","--inspect-tools");
  assert.equal(result.code,1);
  assert.equal((result.output.match(/Local tool calls /g) ?? []).length,3);
  assert.equal((result.output.match(/read-fast — returned/g) ?? []).length,3);
  assert.equal((result.output.match(/read-delayed — still running/g) ?? []).length,3);
  assert.match(result.output,/0 passed, 3 failed/);
  assert.doesNotMatch(result.output,/read_queue/);
});

test("retains a fast first rejection alongside slower calls and reports limited coverage", t => {
  const run=consumer(t, `
    try {await ctx.tool("initial",()=>{throw Error();});} catch {}
    for(let i=0;i<9;i++) await ctx.tool("retry-"+i,()=>wait(3));
    return true;
  `);
  const result=run("--case","review","--inspect-tools");
  assert.equal(result.code,0);
  assert.equal((result.output.match(/6 of 10/g) ?? []).length,3);
  assert.equal((result.output.match(/#1 initial — rejected/g) ?? []).length,3);
});

test("an error handled inside the tool stays a returned call", t => {
  const run=consumer(t, `
    await ctx.tool("handle-error",async()=>{try{throw Error("internal");} catch {return "fallback";}});
    return false;
  `);
  const result=run("--case","review","--inspect-tools");
  assert.equal(result.code,1);
  assert.equal((result.output.match(/handle-error — returned/g) ?? []).length,3);
  assert.match(result.output,/0 passed, 3 failed/);
  assert.doesNotMatch(result.output,/fallback/);
});

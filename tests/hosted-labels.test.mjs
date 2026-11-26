import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const archiveName = "tokensand-workflow-check-0.1.11.tgz";
const archive = [`./distribution/${archiveName}`, `../packages/${archiveName}`]
  .map(path => fileURLToPath(new URL(path, import.meta.url))).find(path => existsSync(path));
assert.ok(archive, "Missing packaged checker");

function run(t, body, { strict = false, fail = false } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "tokensand-label-test-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  execFileSync("tar", ["-xzf", archive, "-C", dir]);
  writeFileSync(join(dir, "workflow.mjs"), `
    import {appendFileSync} from "node:fs";
    export default {
      cases: [{id:"case-a",input:{}},{id:"case-b",input:{}}],
      checks:[{id:"known",label:"Known score"}], timeoutMs:1000,
      maxCallsPerRun:{baseline:0,candidate:0},
      baseline: async () => { throw Error("baseline must not run"); },
      candidate: async (_,ctx) => {
        const record = () => appendFileSync("visits","x");
        ${body}
        return 1;
      },
      evaluate: () => ({known:${!fail}})
    }`);
  const result = spawnSync(process.execPath, [join(dir,"package/cli.mjs"),"--offline-workflow","./workflow.mjs",
    ...(strict ? ["--require-hosted-labels"] : [])], {cwd:dir,encoding:"utf8"});
  assert.ifError(result.error);
  return {status:result.status,text:result.stdout+result.stderr,visits:existsSync(join(dir,"visits"))?readFileSync(join(dir,"visits"),"utf8"):""};
}

for (const strict of [false,true]) {
  test(`dotted, sensitive-shaped and non-string labels warn once and run each callback once (strict=${strict})`,t=>{
    const result=run(t,`
      await ctx.tool('fixture.private_value',record);
      await ctx.tool('tobs_redactedfixture',record);
      await ctx.step('case-private-value',record);
      await ctx.tool({toString(){ throw Error("raw label touched"); }},record);
    `,{strict});
    assert.equal(result.status,strict ? 2 : 0);
    assert.equal(result.visits.length,8);
    assert.match(result.text,/2 passed, 0 failed/);
    assert.equal(result.text.split("Hosted label warning:").length-1,1);
    assert.doesNotMatch(result.text,/fixture\.private|redactedfixture|case-private-value|raw label touched/);
  });
}

test("strict output limits a successful result to observed labels",t=>{
  const result=run(t,`
    await ctx.tool('a'+'z'.repeat(39),record);
    await ctx.step('a'+'_'.repeat(63),record);
    if(false) await ctx.tool('dormant.invalid',record);
  `,{strict:true});
  assert.equal(result.status,0);
  assert.equal(result.visits.length,4);
  assert.match(result.text,/4 executed candidate calls/);
  assert.doesNotMatch(result.text,/Hosted label warning/);
});
test("an uninstrumented candidate cannot pass a strict label check",t=>{
  const result=run(t,'record();',{strict:true});
  assert.equal(result.status,2);
  assert.match(result.text,/No step or tool labels ran/);
  assert.equal(result.visits.length,2);
});
test("normal local assertions still detect a genuine failure",t=>{
  const result=run(t,"await ctx.tool('xx.invalid',record);",{fail:true});
  assert.equal(result.status,1);
  assert.match(result.text,/0 passed, 2 failed/);
  assert.equal(result.visits.length,2);
});

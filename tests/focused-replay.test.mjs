import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { appendFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";

const packages = mkdtempSync(join(tmpdir(), "tokensand-focused-packages-"));
after(() => rmSync(packages, { recursive: true, force: true }));

function packagedCli(version) {
  const name = "tokensand-workflow-check-" + version + ".tgz";
  const archive = [new URL("./distribution/" + name, import.meta.url), new URL("../packages/" + name, import.meta.url)]
    .map(fileURLToPath).find(existsSync);
  assert.ok(archive, "Missing release archive: " + name);
  const target = join(packages, version);
  mkdirSync(target);
  execFileSync("tar", ["-xzf", archive, "-C", target]);
  return join(target, "package/cli.mjs");
}

const sourceCli = fileURLToPath(new URL("./cli.mjs", import.meta.url));
const cli = existsSync(sourceCli) ? sourceCli : packagedCli("0.1.13");
function fixture(t, name = "observe.workflow.mjs", ids = ["blocks", "also-blocks"]) {
  const cwd = mkdtempSync(join(tmpdir(), "tokensand-focused-"));
  t.after(() => rmSync(cwd, {recursive:true,force:true}));
  writeFileSync(join(cwd,name), `export default {
    maxCallsPerRun:{ candidate:0 }, timeoutMs:2000,
    cases:${JSON.stringify(ids.map(id => ({id,input:0})))}, checks:[{id:"correct",label:"Correct route"}],
    candidate:(_, ctx)=>ctx.tool("route",()=>false), evaluate:({output})=>({correct:output===true})
  };`);
  function run(cmd,args) {
    const result = spawnSync(cmd,args,{cwd,encoding:"utf8", env:{PATH:process.env.PATH,HOME:cwd,TMPDIR:cwd,NO_COLOR:"1"}, timeout:15000});
    assert.ifError(result.error);
    return {code:result.status,text:result.stdout+result.stderr};
  }
  return {cwd,run,check:(...args)=>run(process.execPath,[cli,...args])};
}
function hint(result) {
  const commands = [...result.text.matchAll(/^Replay this case: (.+)$/gm)];
  assert.equal(commands.length,1,result.text);
  return commands[0][1];
}

test("standalone failed check prints a working replay for a quoted workflow path and exact ID", {skip:process.platform === "win32"}, t => {
  const filename = "owner's $WORKFLOW; example.mjs";
  const id = "total O'Reilly; $USD";
  const {run,check} = fixture(t,filename,[id,"unrelated"]);
  const suite = check("--offline-workflow",filename);
  assert.equal(suite.code,1,suite.text);
  const command = hint(suite);
  assert.match(command,/--inspect-tools/);
  const replay=run("sh",["-c",command]);
  assert.equal(replay.code,1,replay.text);
  assert.match(replay.text,/trial 3\/3/);
  assert.ok(replay.text.includes(id));
  assert.doesNotMatch(replay.text,/unrelated|Replay this case:/);
});

test("ordinary installed npm test prints a concrete local command without a global checker", {skip:process.platform === "win32"}, t => {
  const {run,check,cwd}=fixture(t);
  assert.equal(check("--install-test").code,1);
  assert.ok(existsSync(join(cwd,"tokensand-check.mjs")));
  const full=run("npm",["test"]);
  assert.equal(full.code,1,full.text);
  const command=hint(full);
  assert.match(command,/^node \.\/tokensand-check\.mjs .*--case=blocks --inspect-tools$/);
  const replay=run("sh",["-c",command]);
  assert.equal(replay.code,1,replay.text);
  assert.match(replay.text,/trial 3\/3/);
  assert.doesNotMatch(replay.text,/also-blocks/);
});

test("case-only invocation prefers the app and distinguishes unknown and duplicate IDs from a regression", t => {
  const {check,cwd}=fixture(t);
  writeFileSync(join(cwd,"tokensand-first.test.mjs"),'throw Error("wrong demonstration");');
  const found=check("--case","blocks");
  assert.equal(found.code,1,found.text);
  assert.match(found.text,/0 passed, 3 failed/);
  assert.doesNotMatch(found.text,/wrong demonstration/);
  const absent=check("--case","blokcs");
  assert.equal(absent.code,2,absent.text);
  assert.match(absent.text,/Unknown case ID.*"blocks".*"also-blocks"/);
  assert.doesNotMatch(absent.text,/ERR_MODULE_NOT_FOUND|Replay this case:/);
  const duplicate=fixture(t,"observe.workflow.mjs",["same","same"]).check("--case","same");
  assert.equal(duplicate.code,2,duplicate.text);
  assert.match(duplicate.text,/duplicated/);
  rmSync(join(cwd,"tokensand-first.test.mjs")); rmSync(join(cwd,"observe.workflow.mjs"));
  const missing=check("--case","blocks");
  assert.equal(missing.code,2,missing.text);
  assert.match(missing.text,/--offline-workflow/);
});

test("terminal controls never appear in a replay command or the list of available IDs", t => {
  const id="case\u001b[31m";
  const {check}=fixture(t,"observe.workflow.mjs",[id,"valid"]);
  const full=check("--offline-workflow","./observe.workflow.mjs");
  assert.equal(full.code,1);
  assert.doesNotMatch(full.text,/^Replay this case: /m);
  assert.match(full.text,/cannot be safely printed/);
  const missing=check("--case","typo");
  assert.equal(missing.code,2);
  assert.match(missing.text,/"valid"/);
  assert.ok(!missing.text.includes(id));
});

test("upgrades the exact released runner and preserves a teammate's customized runner", t => {
  const previous = packagedCli("0.1.12");
  for (const edited of [false, true]) {
    const { check, cwd, run } = fixture(t);
    const old = run(process.execPath, [previous, "--install-test"]);
    assert.equal(old.code, 1, old.text);
    const installed = join(cwd, "tokensand-check.mjs");
    if (edited) appendFileSync(installed, "\n// Local team customization.\n");
    const priorRunner = readFileSync(installed);
    const manifest = readFileSync(join(cwd, "package.json"));
    const upgrade = check("--install-test");
    if (edited) {
      assert.equal(upgrade.code, 2, upgrade.text);
      assert.deepEqual(readFileSync(installed), priorRunner);
      assert.equal(existsSync(join(cwd, ".github")), false);
    } else {
      assert.equal(upgrade.code, 1, upgrade.text);
      assert.notDeepEqual(readFileSync(installed), priorRunner);
      const next = run("npm", ["test"]);
      assert.equal(next.code, 1, next.text);
      assert.match(hint(next), /--case=blocks --inspect-tools/);
    }
    assert.deepEqual(readFileSync(join(cwd, "package.json")), manifest);
  }
});

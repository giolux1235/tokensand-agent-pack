import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { appendFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
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
const cli = existsSync(sourceCli) ? sourceCli : packagedCli("0.1.18");
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

test("the whole workflow catches a failure on a later configured trial", t => {
  const { cwd, check } = fixture(t);
  const path = join(cwd, "observe.workflow.mjs");
  const writeWorkflow = (repetitions, fixed) => writeFileSync(path, `export default {
    ${repetitions} maxCallsPerRun: {candidate: 0},
    cases: [{ id: "control" }, { id: "search" }],
    checks: [{ id: "found", label: "Return a matching document" }],
    candidate: (_, ctx) => ${fixed} || ctx.caseId === "control" || ctx.repetition !== 1,
    evaluate: ({output}) => ({ found: output })
  };`);
  const args = ["--offline-workflow", "./observe.workflow.mjs"];
  writeWorkflow("repetitions: 3,", false);
  const broken = check(...args);
  assert.equal(broken.code, 1, broken.text);
  assert.match(broken.text, /FAIL search \(trial 2\/3\)/);
  assert.match(broken.text, /5 passed, 1 failed/);
  writeWorkflow("repetitions: 3,", true);
  assert.match(check(...args).text, /6 passed, 0 failed/);
  writeWorkflow("repetitions: 5,", true);
  assert.match(check(...args, "--case=search").text, /5 passed, 0 failed/);
  writeWorkflow("", true);
  assert.match(check(...args).text, /2 passed, 0 failed/);
  assert.match(check(...args, "--case=search").text, /3 passed, 0 failed/);
  for (const invalid of ["0", "11", "null", "true", "'3'", "1.5"]) {
    writeWorkflow("repetitions: " + invalid + ",", false);
    const result = check(...args);
    assert.equal(result.code, 2, result.text);
    assert.match(result.text, /workflow repetitions/);
    assert.doesNotMatch(result.text, /\d+ passed/);
  }
});

function selectionFixture(t) {
  const app = fixture(t);
  const file = join(app.cwd, "observe.workflow.mjs");
  const calls = join(app.cwd, "executed.jsonl");
  writeFileSync(file, `import { appendFileSync } from "node:fs";
    export default {maxCallsPerRun:{candidate:0}, checks:[{id:"correct",label:"Correct route"}],
      cases:["passes","fails","--literal", "ambiguous", "ambiguous"].map(id=>({id,input:id})),
      candidate:(input,ctx)=>{
        appendFileSync("executed.jsonl", JSON.stringify([ctx.caseId,ctx.repetition])+"\\n");
        return input !== "fails";
      },evaluate:({output})=>({correct:output})};`);
  return { ...app, calls, takeCalls() {
    if (!existsSync(calls)) return [];
    const observed = readFileSync(calls,"utf8").trim().split("\n").map(line => JSON.parse(line));
    rmSync(calls);
    return observed;
  }};
}

test("every explicit case runs three times in argument order on the CLI and standalone checker", t => {
  const { check, run, takeCalls } = selectionFixture(t);
  const standalone = join(dirname(cli), "agent-check.mjs");
  for (const invoke of [check, (...args) => run(process.execPath, [standalone, ...args])]) {
    for (const ids of [["fails","passes"], ["passes","fails"], ["--literal","passes"]]) {
      const result = invoke("--offline-workflow", "./observe.workflow.mjs", "--case=" + ids[0], "--case", ids[1]);
      assert.equal(result.code,ids.includes("fails") ? 1 : 0,result.text);
      assert.deepEqual(takeCalls(),ids.flatMap(id=>[0,1,2].map(repetition=>[id,repetition])));
      if (ids.includes("fails")) assert.match(result.text,/fails \(trial 3\/3\)/);
    }
  }
});

test("invalid case, path and detail selections fail before any candidate callback", t => {
  const { check, takeCalls } = selectionFixture(t);
  for (const [args, message] of [
    [["--case=passes","--case=missing"], /Unknown case ID "missing"/],
    [["--case=missing","--case=passes"], /Unknown case ID "missing"/],
    [["--case=passes","--case=ambiguous"], /"ambiguous" is duplicated/],
    [["--case=passes","--case=passes"], /requested more than once/],
    [["--case=passes","--case="], /case ID/],
    [["--case=passes","--case"], /--case=ID/],
    [["--offline-workflow=missing.mjs","--offline-workflow","./observe.workflow.mjs"], /workflow once/],
    ...["--inspect-tools","--inspect-values"].map(flag => [["--case=fails","--case=passes",flag], /exactly one case/]),
  ]) {
    const result = check(...args);
    assert.equal(result.code,2,result.text);
    assert.match(result.text,message);
    assert.deepEqual(takeCalls(),[]);
    assert.doesNotMatch(result.text,/passed,|Private case values:|ERR_MODULE_NOT_FOUND/);
  }
});

test("upgrades the shipped 0.1.16 runner without overwriting a customized copy", t => {
  const previous = packagedCli("0.1.16");
  for (const edited of [true,false]) {
    const { check, cwd, run, takeCalls } = selectionFixture(t);
    assert.equal(run(process.execPath,[previous,"--install-test"]).code,1);
    takeCalls();
    const runner = join(cwd,"tokensand-check.mjs");
    if (edited) appendFileSync(runner,"\n// Custom team workflow.\n");
    const before = readFileSync(runner);
    const update = check("--install-test");
    if (edited) {
      assert.equal(update.code,2,update.text);
      assert.deepEqual(readFileSync(runner),before);
      assert.deepEqual(takeCalls(),[]);
      continue;
    }
    assert.equal(update.code,1,update.text);
    takeCalls();
    const retry = run(process.execPath,[runner,"--case=fails","--case=passes"]);
    assert.equal(retry.code,1,retry.text);
    assert.deepEqual(takeCalls(),["fails","passes"].flatMap(id=>[0,1,2].map(repetition=>[id,repetition])));
  }
});

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
      assert.deepEqual(readFileSync(join(cwd, "package.json")), manifest);
      assert.equal(existsSync(join(cwd, ".github")), false);
    } else {
      assert.equal(upgrade.code, 1, upgrade.text);
      assert.notDeepEqual(readFileSync(installed), priorRunner);
      const oldWatch = "node --watch tokensand-check.mjs";
      assert.equal(JSON.parse(manifest).scripts["test:tokensand:watch"], oldWatch);
      const expected = manifest.toString().replace(JSON.stringify(oldWatch), JSON.stringify("node tokensand-check.mjs --watch-workflow"));
      assert.equal(readFileSync(join(cwd, "package.json"), "utf8"), expected);
      const next = run("npm", ["test"]);
      assert.equal(next.code, 1, next.text);
      assert.match(hint(next), /--case=blocks --inspect-tools/);
    }
  }
});

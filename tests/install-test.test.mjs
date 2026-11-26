import assert from "node:assert/strict";
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const name = "tokensand-workflow-check-0.1.15.tgz";
const archive = [new URL("../packages/" + name, import.meta.url), new URL("./distribution/" + name, import.meta.url)]
  .map(fileURLToPath).find(existsSync);
const adapter = `import decide from "./app.mjs";
export default {id:"route-change",maxCallsPerRun:{candidate:0},cases:[{id:"route-known",input:1},{id:"route-unknown",input:0}],
  checks:[{id:"route",label:"Correct route"}],candidate:(input)=>decide(input),
  evaluate:({input,output})=>({route:output===Boolean(input)})};`;
function setup(t) {
  const root = mkdtempSync(join(tmpdir(), "tokensand-persist-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const repo = join(root, "repo");
  const home = join(root, "home");
  mkdirSync(repo); mkdirSync(home);
  execFileSync("tar", ["-xzf", archive, "-C", root]);
  const env = { PATH: process.env.PATH, HOME: home, TMPDIR: root, CI: "true", DO_NOT_TRACK: "1",
    npm_config_offline: "true", npm_config_userconfig: "/dev/null", npm_config_cache: join(root,"empty-cache"),
    npm_config_update_notifier: "false", npm_config_audit: "false" };
  const run = (command, args, cwd = repo, options = {}) => {
    const r = spawnSync(command, args, { cwd, encoding:"utf8", env:{...env,...options}, timeout:20000 });
    assert.ok(!r.error, r.error?.message);
    return { status:r.status, output:r.stdout+r.stderr };
  };
  const install = (...flags) => run(process.execPath,[join(root,"package/cli.mjs"),"--install-test",...flags]);
  const file = (name, content) => writeFileSync(join(repo,name),content);
  const text = name => readFileSync(join(repo,name),"utf8");
  const fixture = () => { file("observe.workflow.mjs",adapter); file("app.mjs","export default () => true;"); };
  return {root,repo,run,install,file,text,fixture,env};
}

test("a failed app change stays in npm test across edits, reinstall and a clean teammate checkout", t => {
  const {root,repo,run,install,file,text,fixture} = setup(t);
  fixture();
  const existing = "node -e \"console.log('existing app suite')\"";
  file("package.json", JSON.stringify({private:true,scripts:{test:existing}},null,2)+"\n");
  assert.equal(run("npm",["test"]).status,0);
  const failed = install();
  assert.equal(failed.status,1,failed.output);
  assert.match(failed.output,/FAIL route-unknown.*Correct route/);
  const manifest = text("package.json");
  assert.equal(JSON.parse(manifest).scripts.test,existing);
  assert.equal(JSON.parse(manifest).scripts.pretest,"node tokensand-check.mjs --npm-test-hook");
  assert.equal(install().status,1);
  assert.equal(text("package.json"),manifest);
  const blocked = run("npm",["test"]);
  assert.equal(blocked.status,1,blocked.output);
  assert.match(blocked.output,/route-unknown/);
  file("app.mjs","export default value => Boolean(value);");
  const fixed = run("npm",["test"]);
  assert.equal(fixed.status,0,fixed.output);
  assert.match(fixed.output,/route-unknown/);
  assert.match(fixed.output,/existing app suite/);
  const git = (...args) => execFileSync("git",args,{cwd:repo,stdio:"pipe"});
  git("init","-q"); git("add","package.json","tokensand-check.mjs","app.mjs","observe.workflow.mjs");
  git("-c","user.name=Fixture","-c","user.email=test@example.invalid","commit","-qm","check workflow");
  const teammate = join(root,"teammate");
  git("clone","-q","--no-local",repo,teammate);
  const fresh = run("npm",["test"],teammate,{GITHUB_ACTIONS:"true",GITHUB_EVENT_NAME:"push"});
  assert.equal(fresh.status,0,fresh.output);
  assert.match(fresh.output,/route-known/);
  assert.equal(existsSync(join(teammate,"node_modules")),false);
  const nested = join(teammate,"src/nested");
  mkdirSync(nested,{recursive:true});
  const nestedTest = run("npm",["test"],nested);
  assert.equal(nestedTest.status,0,nestedTest.output);
  assert.match(nestedTest.output,/route-known/);
  const directNested = run(process.execPath,[join(teammate,"tokensand-check.mjs")],nested);
  assert.equal(directNested.status,0,directNested.output);
  assert.match(directNested.output,/route-known/);
  writeFileSync(join(teammate,"app.mjs"),'throw Error("must not execute on fork");');
  for (const event of ["pull_request","pull_request_target"]) {
    const fork = run("npm",["run","test:tokensand"],teammate,{GITHUB_ACTIONS:"true",GITHUB_EVENT_NAME:event});
    assert.equal(fork.status,2,fork.output);
    assert.match(fork.output,/unavailable.*no cases evaluated/);
    const originalSuite = run("npm",["test"],teammate,{GITHUB_ACTIONS:"true",GITHUB_EVENT_NAME:event});
    assert.equal(originalSuite.status,0,originalSuite.output);
    assert.match(originalSuite.output,/no cases evaluated/);
    assert.match(originalSuite.output,/existing app suite/);
    assert.doesNotMatch(fork.output,/route-known|must not execute on fork/);
  }
});

test("missing setup and name collisions preserve the repository", t => {
  const {repo,file,fixture,install,text} = setup(t);
  const missing = install();
  assert.equal(missing.status,2);
  assert.match(missing.output,/directory containing observe.workflow.mjs.*create observe.workflow.mjs/);
  assert.equal(existsSync(join(repo,"package.json")),false);
  assert.equal(existsSync(join(repo,"tokensand-check.mjs")),false);
  fixture();
  const conflicting = JSON.stringify({scripts:{"test:tokensand":"node custom.mjs"}});
  file("package.json",conflicting);
  assert.equal(install().status,2);
  assert.equal(text("package.json"),conflicting);
  assert.equal(existsSync(join(repo,"tokensand-check.mjs")),false);
  file("package.json","{}");
  file("tokensand-check.mjs","// existing user code\n");
  assert.equal(install().status,2);
  assert.equal(text("tokensand-check.mjs"),"// existing user code\n");
  assert.equal(text("package.json"),"{}");
});

test("a project without package.json gains a portable check and a positive provider budget fails", t => {
  const {repo,run,file,fixture,install,text} = setup(t);
  fixture();
  file("app.mjs","export default value => Boolean(value);");
  assert.equal(install().status,0);
  assert.equal(JSON.parse(text("package.json")).scripts.test,"npm run test:tokensand");
  assert.equal(run("npm",["test"],repo,{GITHUB_ACTIONS:"true",GITHUB_EVENT_NAME:"pull_request"}).status,2);
  assert.equal(JSON.parse(text("package.json")).scripts["test:tokensand:watch"],"node tokensand-check.mjs --watch-workflow");
  file("observe.workflow.mjs",adapter.replace("candidate:0","candidate:1"));
  const unconfigured = run("npm",["test"]);
  assert.equal(unconfigured.status,1);
  assert.match(unconfigured.output,/provider budget to zero/);
  rmSync(join(repo,"observe.workflow.mjs"));
  const deleted = run(process.execPath,["tokensand-check.mjs"]);
  assert.equal(deleted.status,2);
  assert.doesNotMatch(deleted.output,/ERR_MODULE_NOT_FOUND/);
  assert.match(deleted.output,/observe.workflow.mjs/);
  assert.doesNotMatch(deleted.output,/retried_call/);
});

test("wraps two occupied lifecycle hooks while preserving failures and the custom test", t => {
  const {run,file,fixture,install,text} = setup(t);
  fixture();
  const scripts={pretest:"node -e \"console.log('original pre')\" || true",test:"node -e \"console.log('original test')\"",posttest:"node -e \"console.log('original post')\""};
  file("package.json",JSON.stringify({scripts},null,2).replaceAll("\n","\r\n")+"\r\n");
  assert.equal(install().status,1);
  assert.equal(run("npm",["test"]).status,1);
  const saved = text("package.json");
  assert.ok(saved.includes("\r\n"));
  assert.equal(JSON.parse(saved).scripts.posttest,scripts.posttest);
  file("app.mjs","export default value => Boolean(value);");
  assert.equal(install().status,0);
  assert.equal(text("package.json"),saved);
  const fixed = run("npm",["test"]);
  assert.equal(fixed.status,0,fixed.output);
  for (const suffix of ["pre","test","post"]) assert.ok(fixed.output.includes("original "+suffix));
});

test("a saved app repair reruns in the same watch process", {skip:process.platform === "win32",timeout:15000}, async t => {
  const {repo,file,fixture,install,env} = setup(t);
  fixture();
  assert.equal(install().status,1);
  const child = spawn("npm",["run","test:tokensand:watch"],{cwd:repo,env,detached:true,stdio:["ignore","pipe","pipe"]});
  let log = "";
  const collect = chunk => { log += chunk.toString(); };
  child.stdout.on("data",collect); child.stderr.on("data",collect);
  async function waitFor(pattern) {
    const deadline = Date.now()+5000;
    while (!pattern.test(log)) {
      if (Date.now() >= deadline || child.exitCode !== null) assert.fail(log);
      await new Promise(resolve => setTimeout(resolve,25));
    }
  }
  try {
    await waitFor(/Failed running .*tokensand-check.mjs/i);
    assert.match(log,/route-unknown/);
    log="";
    file("app.mjs","export default value => Boolean(value);");
    await waitFor(/Completed running .*tokensand-check.mjs/i);
    assert.match(log,/route-unknown/);
  } finally {
    if (child.exitCode === null) {
      const closed = new Promise(resolve => child.once("close",resolve));
      process.kill(-child.pid,"SIGTERM");
      await closed;
    }
  }
});

test("ordinary and atomic syntax repairs re-run only when a local workflow source changes", { timeout:20000 }, async t => {
  const {repo,env,file,fixture,install} = setup(t);
  fixture();
  const valid = "export default value => Boolean(value);";
  file("app.mjs", valid);
  assert.equal(install().status,0);
  const child = spawn(process.execPath, ["tokensand-check.mjs","--watch-workflow"], {cwd:repo,env,stdio:["ignore","pipe","pipe"]});
  let log = "";
  child.stdout.on("data", chunk => { log += chunk; });
  child.stderr.on("data", chunk => { log += chunk; });
  async function verdict(state) {
    const end = Date.now() + 4500;
    while (!log.includes(state + " running tokensand-check.mjs")) {
      assert.ok(Date.now() < end && child.exitCode === null, log);
      await new Promise(resolve => setTimeout(resolve,25));
    }
    const output = log;
    log = "";
    return output;
  }
  try {
    await verdict("Completed");
    file(".env.local","unused=true");
    file("unrelated.mjs","export default false;");
    await new Promise(resolve => setTimeout(resolve,320));
    assert.doesNotMatch(log,/running tokensand-check/);
    for (const atomic of [false,true]) {
      const save = source => {
        if (!atomic) return file("app.mjs",source);
        file("next.mjs",source);
        renameSync(join(repo,"next.mjs"),join(repo,"app.mjs"));
      };
      save("export default (;"); assert.match(await verdict("Failed"),/SyntaxError/);
      save(valid); assert.match(await verdict("Completed"),/route-unknown/);
    }
    file("observe.workflow.mjs", "import './';");
    assert.match(await verdict("Failed"),/unsupported|directory import/i);
    file("observe.workflow.mjs", adapter);
    assert.match(await verdict("Completed"),/route-unknown/);
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      const close = new Promise(resolve => child.once("close",resolve));
      child.kill();
      await close;
    }
  }
});

test("upgrades published 0.1.14 watch setup and preserves custom commands and runner edits", t => {
  const oldName = "tokensand-workflow-check-0.1.14.tgz";
  const oldArchive = [new URL("../packages/" + oldName, import.meta.url),new URL("./distribution/" + oldName, import.meta.url)]
    .map(fileURLToPath).find(existsSync);
  for (const customization of ["none","runner","watch"]) {
    const {root,repo,run,install,file,text,fixture} = setup(t);
    const previous = join(root,"previous");
    mkdirSync(previous);
    execFileSync("tar",["-xzf",oldArchive,"-C",previous]);
    fixture();
    file("package.json",JSON.stringify({scripts:{test:"node native.cjs",pretest:"node lint.cjs"}}));
    assert.equal(run(process.execPath,[join(previous,"package/cli.mjs"),"--install-test"]).status,1);
    if (customization === "runner") file("tokensand-check.mjs",text("tokensand-check.mjs")+"\n// Team customization.\n");
    if (customization === "watch") {
      const manifest = JSON.parse(text("package.json"));
      manifest.scripts["test:tokensand:watch"] = "node custom-watch.mjs";
      file("package.json",JSON.stringify(manifest));
    }
    const before = text("package.json"), oldRunner = text("tokensand-check.mjs");
    const after = install();
    if (customization !== "none") {
      assert.equal(after.status,2,after.output);
      assert.equal(text("package.json"),before); assert.equal(text("tokensand-check.mjs"),oldRunner);
      continue;
    }
    assert.equal(after.status,1,after.output); assert.match(after.output,/route-unknown/);
    const scripts = JSON.parse(text("package.json")).scripts;
    assert.deepEqual(scripts,{...JSON.parse(before).scripts,"test:tokensand:watch":"node tokensand-check.mjs --watch-workflow"});
    const upgraded = text("tokensand-check.mjs");
    assert.notEqual(upgraded,oldRunner);
    assert.equal(install().status,1);
    assert.equal(text("tokensand-check.mjs"),upgraded);
  }
});

test("optional GitHub setup installs the packaged workflow once and preserves an existing distinct verifier", t => {
  const {root,repo,file,fixture,install,text} = setup(t);
  fixture();
  file("app.mjs","export default value => Boolean(value);");
  assert.equal(install().status,0);
  assert.equal(existsSync(join(repo,".github")),false);
  assert.equal(install("--github").status,2);
  execFileSync("git",["init","-q"],{cwd:repo});
  mkdirSync(join(repo,".github/workflows"),{recursive:true});
  file(".github/workflows/other.yml","# existing verifier");
  for (let i=0;i<2;i++) {
    const r=install("--github");
    assert.equal(r.status,0,r.output);
    assert.equal(text(".github/workflows/tokensand-evaluate.yml"),readFileSync(join(root,"package/fresh-pr.yml"),"utf8"));
    assert.equal(text(".github/workflows/other.yml"),"# existing verifier");
  }
});

test("an existing custom GitHub workflow or symlink aborts all installation writes", t => {
  for (const collision of ["custom file", ".github link", "workflows link", "workflow link"]) {
    const {root,repo,file,fixture,install,text} = setup(t);
    fixture();
    file("package.json","{}");
    const outside=join(root,"outside");
    execFileSync("git",["init","-q"],{cwd:repo});
    mkdirSync(outside);
    if (collision === ".github link") symlinkSync(outside,join(repo,".github"));
    else {
      mkdirSync(join(repo,".github"));
      if (collision === "workflows link") symlinkSync(outside,join(repo,".github/workflows"));
      else {
        mkdirSync(join(repo,".github/workflows"));
        if (collision === "workflow link") {
          writeFileSync(join(outside,"original.yml"),"custom");
          symlinkSync(join(outside,"original.yml"),join(repo,".github/workflows/tokensand-evaluate.yml"));
        } else file(".github/workflows/tokensand-evaluate.yml","custom");
      }
    }
    const r=install("--github");
    assert.equal(r.status,2,collision+": "+r.output);
    assert.equal(text("package.json"),"{}");
    assert.equal(existsSync(join(repo,"tokensand-check.mjs")),false);
    assert.equal(existsSync(join(outside,"tokensand-evaluate.yml")),false);
    if (collision === "custom file" || collision === "workflow link") assert.equal(text(".github/workflows/tokensand-evaluate.yml"),"custom");
  }
});

test("GitHub setup rejects nested packages and ignored output before modifying either app", t => {
  const {root,repo,run,file,text,fixture,install} = setup(t);
  execFileSync("git",["init","-q"],{cwd:repo});
  fixture();
  file("package.json","{}");
  file(".gitignore",".github/\n");
  const ignored = install("--github");
  assert.equal(ignored.status,2);
  assert.match(ignored.output,/Git ignores/);
  assert.equal(text("package.json"),"{}");
  assert.equal(existsSync(join(repo,".github")),false);
  assert.equal(existsSync(join(repo,"tokensand-check.mjs")),false);
  const nested = join(repo,"packages/app");
  mkdirSync(nested,{recursive:true});
  writeFileSync(join(nested,"observe.workflow.mjs"),adapter);
  writeFileSync(join(nested,"package.json"),"{}");
  const sub = run(process.execPath,[join(root,"package/cli.mjs"),"--install-test","--github"],nested);
  assert.equal(sub.status,2);
  assert.match(sub.output,/repository root/);
  assert.equal(readFileSync(join(nested,"package.json"),"utf8"),"{}");
  assert.equal(existsSync(join(nested,".github")),false);
  assert.equal(existsSync(join(nested,"tokensand-check.mjs")),false);
});

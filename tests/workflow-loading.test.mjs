import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const runner = fileURLToPath(new URL("../plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/check_workflow.mjs", import.meta.url));
const adapter = "observe.workflow.mjs";

function service(t) {
  const cwd = mkdtempSync(join(tmpdir(), "tokensand-workflow-load-"));
  t.after(() => rmSync(cwd, { recursive: true, force: true }));
  return {
    write(name, source) { writeFileSync(join(cwd, name), source); },
    directory(name) { mkdirSync(join(cwd, name)); },
    run(path, expected) {
      const args = path === undefined ? [] : ["--offline-workflow", path];
      const result = spawnSync(process.execPath, [runner, ...args], { cwd, env: { PATH: process.env.PATH }, encoding: "utf8", timeout: 8000 });
      assert.ifError(result.error);
      const output = result.stdout + result.stderr;
      assert.equal(result.status, expected, output);
      if (expected === 2) {
        assert.doesNotMatch(output, /PRIVATE|node:internal|\n\s+at |TAP version/i);
        assert(!output.includes(cwd));
        assert.equal(result.stdout, "");
        assert.match(result.stderr, /No cases ran/);
      }
      return output;
    },
  };
}

test("missing selected module points to the app path or a pasteable offline setup", t => {
  const app = service(t);
  const message = app.run("./PRIVATE-missing.mjs", 2);
  assert.match(message, /workflow file not found/i);
  assert.match(message, /--offline-workflow/);
  assert.match(message, /releases\/download\/workflow-check-[\d.]+\/observe\.workflow\.mjs/);
  assert.match(message, /Create observe\.workflow\.mjs.*zero provider/i);
  app.directory("PRIVATE-folder");
  assert.match(app.run("./PRIVATE-folder", 2), /workflow module file/i);
});

test("dependency import and top-level failures have distinct setup outcomes", t => {
  const app = service(t);
  app.write(adapter, 'import "PRIVATE-missing-package";');
  assert.match(app.run(adapter, 2), /dependency|dependencies/i);
  app.write(adapter, 'import "./PRIVATE-not-a-module.mjs";');
  assert.match(app.run(adapter, 2), /dependency|dependencies/i);
  app.write(adapter, 'throw Error("PRIVATE-secret-from-initialization");');
  assert.match(app.run(adapter, 2), /initialize/i);
  app.write(adapter, 'throw {get code(){throw Error("PRIVATE-secret-in-getter")}};');
  assert.match(app.run(adapter, 2), /initialize/i);
  app.write(adapter, 'export default {broken syntax PRIVATE');
  assert.match(app.run(adapter, 2), /initialize|syntax/i);
  app.write(adapter, 'export const unrelated = 2;');
  assert.match(app.run(adapter, 2), /default export/i);
});

test("genuine failed, unscored, throwing and passing candidates still run", t => {
  const app = service(t);
  const code = (expression, evaluate) => `export default {
    maxCallsPerRun:{candidate:0}, cases:[{id:"invoice",input:2}], checks:[{id:"amount"}],
    candidate: n => {${expression}}, evaluate: ({output}) => ({amount:${evaluate}})
  };`;
  for (const [expression,evaluate,expected] of [
    ["return n+1","output===2",1], ["throw Error('local case failed')", "true", 1],
    ["return n","undefined",1], ["return n","output===2",0],
  ]) {
    app.write(adapter, code(expression,evaluate));
    const output = app.run(adapter, expected);
    assert.match(output, /invoice/);
    assert.doesNotMatch(output, /could not initialize|workflow file not found/i);
  }
});

test("the installed guide's first adapter checks the real service against fixed expectations", t => {
  const guide = readFileSync(new URL("../plugins/tokensand-agent-pack/skills/tokensand-agent-pack/SKILL.md", import.meta.url), "utf8");
  const example = guide.match(/```js\n(import \{ summarize \}[^]*?)\n```/);
  assert.ok(example, "Missing copyable order-service adapter");
  const app = service(t);
  app.directory("src");
  app.write(adapter, example[1]);
  app.write("src/orders.mjs", "export const summarize = quantities => ({ units: quantities[0] });");
  const failed = app.run(adapter, 1);
  assert.match(failed, /combined/);
  assert.match(failed, /pass 1/);
  assert.match(failed, /fail 1/);
  app.write("src/orders.mjs", "export const summarize = quantities => ({ units: quantities.reduce((sum, n) => sum + n, 0) });");
  assert.match(app.run(adapter, 0), /pass 2/);
});

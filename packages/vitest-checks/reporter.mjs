import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { realpathSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const claimed = Symbol.for("tokensand.vitest.private-receipt.v1");
const script = fileURLToPath(new URL("./bridge.py", import.meta.url));

function bridge(root, command, payload) {
  const env = {};
  for (const key of ["PATH", "HOME", "LANG", "TMPDIR", "SystemRoot"]) {
    if (process.env[key]) env[key] = process.env[key];
  }
  const run = spawnSync("python3", ["-I", script, command], {
    cwd: root, env, input: payload && JSON.stringify(payload), encoding: "utf8",
    maxBuffer: 8192, timeout: command === "selection" ? 6000 : 35000,
  });
  return run.stdout?.trim() || "delivery unavailable";
}

function ordinaryCounts(module, hooked) {
  if (module.errors().length || [...module.children.allSuites()].some(s => s.errors().length || s.options?.mode === "only")) return;
  let passed = 0, failed = 0;
  for (const test of module.children.allTests()) {
    const outcome = test.result();
    const diag = test.diagnostic();
    if (test.options.fails || test.options.mode === "only" || diag?.retryCount || diag?.repeatCount) return;
    if (outcome.state === "passed" && !outcome.errors?.length) passed++;
    else if (outcome.state === "failed" && !hooked.has(test.id) && outcome.errors?.length &&
      outcome.errors.every(error => /^AssertionError(?: \[ERR_ASSERTION\])?$/.test(error.name))) failed++;
    else return;
  }
  if (passed + failed >= 1 && passed + failed <= 10000) return {passed, failed};
}

export default class PrivateCheckReporter {
  onInit(ctx) {
    if (ctx[claimed]) return;
    ctx[claimed] = true;
    this.ctx = ctx;
  }

  onTestRunStart(specs) {
    this.run = undefined;
    if (!this.ctx || process.env.TOKENSAND_VITEST_RECEIPT) return;
    try {
      const config = this.ctx.config;
      if (config.watch || config.shard || config.bail || config.testNamePattern || config.mergeReports ||
          config.typecheck?.only) return;
      const root = config.root;
      const require = createRequire(resolve(root, "package.json"));
      if (!/^4\.1\.\d+$/.test(require("vitest/package.json").version)) return;
      const grant = JSON.parse(bridge(root, "selection"));
      if (!grant || realpathSync(grant.file) !== grant.file) return;
      const chosen = specs.filter(spec => spec.moduleId === grant.file && spec.project.name === grant.suite);
      if (chosen.length !== 1) return;
      const spec = chosen[0];
      if (spec.testLines?.length || spec.testNamePattern || spec.testIds?.length || spec.testTagsFilter?.length ||
          spec.project.config.testNamePattern || spec.project.config.globalSetup.length || spec.project.config.browser.enabled || spec.project.config.typecheck.enabled) return;
      this.run = {root, token: grant.token, id: randomUUID(), spec, hooked: new Set()};
    } catch { /* A reporting issue cannot fail the developer's test run. */ }
  }

  onHookStart({entity, name}) {
    if (this.run && (name === "beforeEach" || name === "afterEach") && entity.module?.moduleId === this.run.spec.moduleId) {
      this.run.hooked.add(entity.id);
    }
  }

  onTestRunEnd(modules, errors, reason) {
    const run = this.run;
    this.run = undefined;
    if (!run || errors.length || !["passed", "failed"].includes(reason)) return;
    try {
      const selected = modules.filter(m => m.moduleId === run.spec.moduleId && m.project === run.spec.project);
      if (selected.length !== 1) return;
      const counts = ordinaryCounts(selected[0], run.hooked);
      if (!counts) return;
      const status = bridge(run.root, "record", {token:run.token, run:run.id, ...counts});
      // The helper's complete vocabulary is public. Never echo arbitrary subprocess output.
      const label = ["delivered", "queued", "disconnected", "check app connection", "queue full",
        "delivery unavailable", "receipt rejected"].includes(status) ? status : "delivery unavailable";
      process.stderr.write("Tokens& private check " + run.id + ": " + label + " (" + counts.passed +
        " passed, " + counts.failed + " failed).\n");
    } catch { /* Scoring and exit status stay with Vitest. */ }
  }
}

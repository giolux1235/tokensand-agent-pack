import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtempSync, readFileSync, renameSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const command = fileURLToPath(new URL("../plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/inspect_hosted_trial.mjs", import.meta.url));
const canary = "unselected private input or output";
function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), "tokensand-inspect-trial-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const id = randomUUID(), app = randomUUID();
  const url = new URL("https://tokensand.com/dashboard/agents?mode=developer");
  url.searchParams.set("observeProjectId", app);
  url.searchParams.set("buildResult", "experiment:" + app + ":" + id);
  const read = file => JSON.parse(readFileSync(join(dir, file)));
  const save = (file, value) => writeFileSync(join(dir, file), JSON.stringify(value));
  const sha = "0".repeat(64);
  const plan = { schemaVersion: 1, workflow: "stock", timeoutMs: 30000,
    datasetSha256: sha, baselineSha256: sha, candidateSha256: sha, evaluatorSha256: sha, cases: [{ id: "repeat-item", inputSha256: "0".repeat(64) }],
    checks: [{ id: "correct", label: canary }], repetitions: 3 };
  const armed = join(dir, "executed");
  const config = join(dir, "observe.workflow.mjs");
  writeFileSync(config, 'import { writeFileSync } from "node:fs"; writeFileSync(' + JSON.stringify(armed) + ', "executed"); throw Error("must not run");');
  save("plan.json", { schemaVersion: 1, id, config, privateInputKey: canary, plan });
  const rows = [];
  for (let n = 0; n < 3; ++n) {
    for (const variant of n % 2 ? ["candidate", "baseline"] : ["baseline", "candidate"]) {
      const row = { caseId: "repeat-item", repetition: n, variant, status: "ok", startedAt: "2026-01-02T01:00:00.000Z", workflowMs: 12,
        sessionId: randomUUID(), eventIds: [], checks: { correct: variant === "candidate" },
        toolSpans: [{ name: "lookup", id: randomUUID(), startMs: 1, durationMs: 10, status: "ok" }] };
      rows.push({ ...row, providerCalls: 0, providerErrors: 0, estimatedCostUsd: 0, telemetryComplete: true });
      const filename = "run-" + String(rows.length).padStart(3, "0") + ".json";
      save(filename, { run: row, diagnostic: { privateText: canary }, output: { stock: variant === "candidate" ? 1 : 2, title: 'Box "A"', cursors: { east: variant === "candidate" ? 0 : 1, api_key: canary }, items: [{ count: 3, optional: null }], whole: { private: canary }, note: canary } });
    }
  }
  save("result.json", { kind: "experiment", schemaVersion: 1, id, plan, runs: rows, state: "complete" });
  function invoke(selection, result = id) {
    const p = spawnSync(process.execPath, [command, dir, "--result", result, "--case", "repeat-item", ...selection], { encoding: "utf8" });
    assert.doesNotMatch(p.stdout + p.stderr, new RegExp(canary));
    assert.throws(() => readFileSync(armed), { code: "ENOENT" });
    return p;
  }
  return { dir, app, id, url, read, save, invoke, run: (args = [], result) => invoke(["--trial", "2", ...args], result) };
}

test("reads a hosted pair by result URL and one-based repetition with alternating variant order", t => {
  const f = fixture(t);
  const p = f.run([], f.url.href); assert.equal(p.status, 0, p.stderr);
  const out = JSON.parse(p.stdout);
  assert.equal(out.trial, 2); assert.equal(out.resultId, f.id);
  assert.deepEqual(out.baseline, { status: "ok", checks: { correct: false }, workflowMs: 12,
    tools: [{ name: "lookup", status: "ok", startMs: 1, durationMs: 10 }] });
  assert.deepEqual(out.candidate.checks, { correct: true });
  assert.equal(out.candidate.selectedOutput, undefined);
  renameSync(join(f.dir, "plan.json"), join(f.dir, "frozen.json"));
  const byId = f.run(["--field", "stock", "--plan", join(f.dir, "frozen.json")]);
  assert.equal(byId.status, 0, byId.stderr);
  const pair = JSON.parse(byId.stdout);
  assert.equal(pair.baseline.selectedOutput.value, 2); assert.equal(pair.candidate.selectedOutput.value, 1);
});

test("only explicitly selected safe scalars can appear, including strings", t => {
  const f = fixture(t);
  function field(name) {
    const p = f.run(["--field", name]); assert.equal(p.status, 0, p.stderr);
    return JSON.parse(p.stdout).baseline.selectedOutput;
  }
  assert.equal(field("title").value, 'Box "A"');
  assert.equal(field("/cursors/east").value, 1);
  assert.equal(field("/items/0/count").value, 3);
  assert.equal(field("/items/0/optional").value, null);
  for (const key of ["whole", "unknown", "/items/0", "/items/2/count", "/items/length"]) assert.equal(field(key).value, undefined);
  for (const key of ["api_key", "__proto__", "output.raw", "/cursors/api_key", "/items/0/__proto__", "/items/*", "/__proto__/stock"]) assert.equal(f.run(["--field", key]).status, 2);
  const j = f.read("run-004.json");
  for (const value of ["Bearer abc", "abc\n" + canary, "x".repeat(121), "abc\u202edanger"]) {
    j.output.title = value; f.save("run-004.json", j);
    assert.equal(field("title").value, undefined);
  }
});

test("refuses ambiguous, foreign or inconsistent links without reflecting the supplied text", t => {
  const f = fixture(t);
  for (const edit of [u => u.searchParams.append("buildResult", u.searchParams.get("buildResult")),
    u => u.searchParams.set("observeProjectId", randomUUID()), u => u.hostname = "example.com",
    u => u.pathname = "/other", u => u.searchParams.set("buildResult", "workflow:" + f.app + ":" + f.id)]) {
    const url = new URL(f.url); edit(url);
    const p = f.run([], url.href); assert.equal(p.status, 2); assert.equal(p.stdout, "");
  }
  assert.equal(f.run([], randomUUID()).status, 2);
  assert.equal(f.run(["--field", "title", "--field", "stock"]).status, 2);
});

test("rejects conflicting records, incomplete pairs, and missing or linked private artifacts", t => {
  const f = fixture(t);
  const journal = f.read("run-004.json"), result = f.read("result.json");
  for (const change of [
    j => j.run.repetition = 0, j => j.run.variant = "candidate", j => j.run.sessionId = randomUUID(),
    j => j.run.checks.correct = true, j => j.run.toolSpans[0].durationMs = 3,
    j => j.run.toolSpans[0].id = randomUUID(), j => j.run.eventIds = [randomUUID()],
    j => j.run.status = "check_error", j => j.kind = "local-workflow-run"
  ]) {
    const copy = structuredClone(journal); change(copy); f.save("run-004.json", copy);
    const p = f.run(["--field", "stock"]); assert.equal(p.status, 2); assert.equal(p.stdout, "");
  }
  f.save("run-004.json", journal);
  for (const change of [
    r => r.id = randomUUID(), r => r.kind = "local-workflow-comparison", r => r.plan.cases[0].inputSha256 = "1".repeat(64),
    r => r.runs[2].sessionId = r.runs[0].sessionId, r => r.runs[2] = r.runs[1], r => r.runs.pop(),
    r => { r.state = "interrupted"; r.runs = r.runs.slice(0, 3); }
  ]) {
    const copy = structuredClone(result); change(copy); f.save("result.json", copy);
    const p = f.run(); assert.equal(p.status, 2); assert.equal(p.stdout, "");
  }
  f.save("result.json", result);
  rmSync(join(f.dir, "run-004.json"));
  assert.equal(f.run().status, 2);
  f.save("outside.json", journal); symlinkSync(join(f.dir, "outside.json"), join(f.dir, "run-004.json"));
  assert.equal(f.run().status, 2);
});

test("enforces the hosted schema's repetition and trial bounds on both matching copies", t => {
  const f = fixture(t);
  const goodPlan = f.read("plan.json"), goodResult = f.read("result.json");
  for (const change of [p => p.repetitions = 2, p => p.repetitions = 11,
    p => { p.repetitions = 10; p.cases = ["repeat-item", "a", "b", "c"].map(id => ({ ...p.cases[0], id })); }]) {
    const frozen = structuredClone(goodPlan), result = structuredClone(goodResult);
    change(frozen.plan); result.plan = frozen.plan; result.state = "interrupted";
    f.save("plan.json", frozen); f.save("result.json", result);
    assert.equal(f.run().status, 2);
  }
});

test("reports an interrupted pair with a missing original output and an unscored check", t => {
  const f = fixture(t);
  const r = f.read("result.json");
  const j = f.read("run-004.json");
  for (const row of [j.run, r.runs[3]]) {
    row.status = "check_error";
    row.checks.correct = null;
    row.toolSpans[0].status = "error";
  }
  r.state = "interrupted";
  delete j.output;
  f.save("result.json", r); f.save("run-004.json", j);
  const proc = f.run(["--field", "stock"]); assert.equal(proc.status, 0, proc.stderr);
  const out = JSON.parse(proc.stdout);
  assert.equal(out.state, "interrupted");
  assert.equal(out.baseline.checks.correct, null);
  assert.equal(out.baseline.tools[0].status, "error");
  assert.equal(out.baseline.selectedOutput.status, "unavailable");
});

test("pairs all repetitions, unions overlapping spans and uses a strict timing tolerance", t => {
  const f = fixture(t);
  const result = f.read("result.json");
  const span = (startMs, durationMs) => ({ id: randomUUID(), name: "lookup", startMs, durationMs, status: "ok" });
  for (const row of result.runs) {
    row.workflowMs = 45;
    row.toolSpans = row.variant === "baseline" ? [span(10, 5), span(5, 10)]
      : [span(25, row.repetition * 5), span(2, 15)];
  }
  // A timing-only handoff deliberately excludes the private per-trial journals.
  for (let index = 1; index <= 6; index++) rmSync(join(f.dir, "run-00" + index + ".json"));
  f.save("result.json", result);
  const p = f.invoke(["--tool", "lookup"], f.url.href);
  assert.equal(p.status, 0, p.stderr);
  const s = JSON.parse(p.stdout);
  assert.equal(s.comparableTrials, 3); assert.equal(s.plannedTrials, 3);
  assert.deepEqual(s.slowerTrialIds, [2, 3]);
  assert.deepEqual(s.trials.map(t => t.deltaMs), [5, 10, 15]);
  assert.deepEqual(s.trials[0].baseline, { activeMs: 10, calls: 2 });
  assert.deepEqual(JSON.parse(f.invoke(["--tool", "lookup", "--min-delta-ms", "10"]).stdout).slowerTrialIds, [3]);
  assert.equal(f.invoke(["--trial", "2"]).status, 2);
});

test("returns partial coverage with reasons and excludes missing, failed and capped calls", t => {
  const f = fixture(t);
  const before = f.read("result.json");
  function partial(change, trial, arm, reason) {
    const r = structuredClone(before); change(r); f.save("result.json", r);
    const p = f.invoke(["--tool", "lookup"]);
    assert.equal(p.status, 2, p.stderr);
    const s = JSON.parse(p.stdout);
    assert.equal(s.comparableTrials, 2);
    assert.deepEqual(s.slowerTrialIds, []);
    assert.equal(s.trials[trial - 1][arm].unavailable, reason);
    assert.equal(s.trials[trial - 1].deltaMs, null);
  }
  partial(r => r.runs[0].toolSpans[0].name = "renamed", 1, "baseline", "tool not recorded");
  partial(r => r.runs[1].telemetryComplete = false, 1, "candidate", "incomplete telemetry");
  partial(r => r.runs[0].providerCalls++, 1, "baseline", "incomplete telemetry");
  partial(r => r.runs[3].toolSpans[0].status = "error", 2, "baseline", "unfinished or failed tool call");
  partial(r => r.runs[2].status = "timeout", 2, "candidate", "timeout");
  partial(r => r.runs[4].toolSpans = Array.from({ length: 32 }, () => ({ ...r.runs[4].toolSpans[0], id: randomUUID() })), 3, "baseline", "span limit reached");
  partial(r => { r.state = "interrupted"; r.runs.pop(); }, 3, "candidate", "missing trial");
  const unknown = f.invoke(["--tool", "never-called"]);
  assert.equal(JSON.parse(unknown.stdout).comparableTrials, 0);
});

test("refuses mixed selectors, invalid tolerances and malformed timing metadata", t => {
  const f = fixture(t);
  for (const args of [[], ["--tool", "lookup", "--trial", "1"], ["--tool", "lookup", "--field", "stock"],
    ["--trial", "2", "--min-delta-ms", "1"], ["--tool", "tobs_private"]]) {
    assert.equal(f.invoke(args).status, 2);
  }
  for (const threshold of ["-5", "NaN", "Infinity", "180001", "0.1234"]) {
    assert.equal(f.invoke(["--tool", "lookup", "--min-delta-ms", threshold]).status, 2);
  }
  const bad = f.read("result.json"); bad.runs[5].toolSpans[0].startMs = 9000; f.save("result.json", bad);
  const p = f.invoke(["--tool", "lookup"]); assert.equal(p.status, 2); assert.equal(p.stdout, "");
});

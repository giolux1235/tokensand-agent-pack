// Offline starter. Replace planCalls with your own local function returning call IDs.
import assert from "node:assert/strict";
import { appendFileSync, closeSync, mkdtempSync, openSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { inspect, isDeepStrictEqual, parseArgs } from "node:util";
import test from "node:test";
import { pathToFileURL } from "node:url";

function planCalls(calls) {
  return calls.map(call => call.id); // Bug: a redelivered call gets scheduled twice.
}

const first = { id: "call-1", tool: "reminder" };
const second = { id: "call-2", tool: "reminder" };
const cases = [
  { id: "one_call", input: { calls: [first], expected: ["call-1"] } },
  { id: "retried_call", input: { calls: [first, first], expected: ["call-1"] } },
  { id: "distinct_calls_same_tool", input: { calls: [first, second], expected: ["call-1", "call-2"] } },
];
const candidate = ({ calls }) => planCalls(calls);

// This definition also works as observe.workflow.mjs with the connected SDK.
const workflow = {
  id: "tool-call-retries", title: "Tool call retries", scope: "synthetic",
  cases, candidate,
  baseline: ({ calls }) => [...new Set(calls.map(call => call.id))],
  evaluate: ({ input, output }) => ({ planned_calls: isDeepStrictEqual(output, input.expected) }),
  checks: [{ id: "planned_calls", label: "Schedule each distinct call once" }],
  checksVersion: "1", repetitions: 3, timeoutMs: 1000,
  maxCallsPerRun: { baseline: 0, candidate: 0 }, sourceFiles: [], evaluatorSourceFiles: [],
};

export default workflow;

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  const { values: { "offline-workflow": adapter, case: caseId, "inspect-values": inspectValues, "require-hosted-labels": requireHostedLabels } } = parseArgs({
    options: { "offline-workflow": { type: "string" }, case: { type: "string" }, "inspect-values": { type: "boolean" }, "require-hosted-labels": { type: "boolean" } },
  });
  assert.ok(adapter !== "" && caseId !== "", "Supply a workflow path or case ID after its option.");
  assert.ok(!inspectValues || caseId, "--inspect-values requires --case CASE_ID.");
  // Only load a trusted local adapter explicitly selected by the developer.
  const selected = adapter ? (await import(pathToFileURL(adapter).href)).default : workflow;
  assert.ok(selected?.maxCallsPerRun?.candidate === 0, "Set the offline candidate provider budget to zero.");
  assert.ok(Array.isArray(selected.cases) && selected.cases.length, "Provide at least one case.");
  assert.ok(Array.isArray(selected.checks) && selected.checks.length, "Provide at least one named check.");
  const items = caseId ? selected.cases.filter(item => item.id === caseId) : selected.cases;
  const trials = caseId ? 3 : 1;
  if (caseId) {
    assert.equal(items.length, 1, `Expected one workflow case with the exact ID ${JSON.stringify(caseId)}. Found ${items.length}.`);
    console.log(`Checking only ${JSON.stringify(caseId)} for ${trials} trials. Run the full suite before sharing a result.`);
  }
  let valuesFile;
  if (inspectValues) {
    valuesFile = join(mkdtempSync(join(tmpdir(), "tokensand-case-")), "values.jsonl");
    closeSync(openSync(valuesFile, "wx", 0o600));
    console.log("Private case values: " + valuesFile);
  }
  function preview(value) {
    return inspect(value, { customInspect: false, getters: false, depth: 2,
      maxArrayLength: 12, maxStringLength: 256, colors: false }).slice(0, 2048);
  }
  let observedLabels = 0;
  let invalidHostedLabel = false;
  function checkLabel(kind, label) {
    observedLabels++;
    const valid = typeof label === "string" && (kind === "step"
      ? /^[a-z][a-z0-9_]{0,63}$/.test(label)
      : /^[a-z][a-z0-9_-]{0,39}$/.test(label) && !/(?:^|[^a-z0-9])(?:sk[-_]|tobs_|dai_|tvly-)/i.test(label));
    if (!valid && !invalidHostedLabel) {
      invalidHostedLabel = true;
      console.error("Hosted label warning: an executed step or tool label would be rejected. Use a fixed, non-sensitive lowercase name starting with a letter. Tools allow digits, hyphens and underscores (40 characters total). Steps allow digits and underscores (64 total). For example, ctx.tool('inventory-get', callback). Rerun with --require-hosted-labels.");
    }
  }
  let interrupted = false;
  test.after(() => {
    if (requireHostedLabels) {
      if (invalidHostedLabel || !observedLabels) {
        console.error(invalidHostedLabel ? "Hosted label check failed." : "No step or tool labels ran. Exercise an instrumented candidate path.");
        process.exitCode ||= 2;
      } else {
        console.log(`Hosted label syntax passed for ${observedLabels} executed candidate calls. A connected run checks the baseline and hosted scoring.`);
      }
    }
    if (!interrupted) return;
    // A callback can ignore its abort signal and leave a socket open. Allow
    // normal teardown and reporter completion first. Successful tests must
    // retain Node's normal handling of late asynchronous errors.
    setTimeout(() => {
      console.error("The interrupted workflow still has work running. Pass ctx.signal to tool requests and close open connections before retrying.");
      process.exit(1);
    }, 1000).unref();
  });
  function registerCase(item, repetition) {
    const name = caseId ? `${item.id} (trial ${repetition + 1}/${trials})` : item.id;
    test(name, { timeout: selected.timeoutMs }, async (t) => {
      // Execute the adapter's local callbacks without recording or sending spans.
      const executeLocal = async (kind, label, callback) => {
        t.signal.throwIfAborted();
        checkLabel(kind, label);
        return callback();
      };
      let attemptedFetch = false;
      const context = {
        caseId: item.id, repetition, sessionId: randomUUID(), signal: t.signal,
        step: (label, callback) => executeLocal("step", label, callback),
        tool: (label, callback) => executeLocal("tool", label, callback),
        fetch: async () => {
          attemptedFetch = true;
          throw new Error("Provider transport is disabled for this offline check.");
        },
      };
      const onAbort = () => { interrupted = true; };
      t.signal.addEventListener("abort", onAbort, { once: true });
      const started = performance.now();
      try {
        const output = await selected.candidate(structuredClone(item.input), context);
        assert.ok(!attemptedFetch, "Provider transport is disabled for this offline check.");
        if (valuesFile) {
          t.signal.throwIfAborted();
          const row = { trial: repetition + 1, actual: preview(output) };
          if (item.input != null && Object.hasOwn(item.input, "expected")) row.expected = preview(item.input.expected);
          appendFileSync(valuesFile, JSON.stringify(row) + "\n");
        }
        let scores;
        try {
          scores = await selected.evaluate({ input: structuredClone(item.input), output, variant: "candidate" });
        } catch {
          // No scorer map was returned. Each declared check remains unscored.
        }
        const failed = [], unscored = [];
        for (const check of selected.checks) {
          t.signal.throwIfAborted();
          const label = check.label || check.id;
          try {
            const score = scores && Object.hasOwn(scores, check.id) ? scores[check.id] : undefined;
            const value = typeof score === "function" ? await score() : score;
            if (value === false) failed.push(label);
            else if (value !== true) unscored.push(label);
          } catch {
            unscored.push(label);
          }
        }
        // A synchronous callback can finish before Node gets a turn to fire its timer.
        const elapsed = performance.now() - started;
        if (Number.isFinite(selected.timeoutMs) && elapsed > selected.timeoutMs) {
          failed.push(`Case took ${Math.ceil(elapsed)} ms (deadline ${selected.timeoutMs} ms)`);
        }
        if (unscored.length) {
          const reasons = failed.length ? [`Failed checks: ${failed.join(", ")}`] : [];
          reasons.push(`Unscored checks: ${unscored.join(", ")}`);
          const error = new Error(reasons.join(". "));
          error.code = "TOKENSAND_UNSCORED";
          throw error;
        }
        assert.equal(failed.length, 0, failed.join(", "));
      } finally {
        t.signal.removeEventListener("abort", onAbort);
      }
    });
  }
  for (const item of items) {
    for (let repetition = 0; repetition < trials; repetition++) registerCase(item, repetition);
  }
}

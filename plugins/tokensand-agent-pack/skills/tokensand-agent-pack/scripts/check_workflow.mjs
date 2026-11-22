// Offline starter. Replace planCalls with your own local function returning call IDs.
import assert from "node:assert/strict";
import { realpathSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { isDeepStrictEqual, parseArgs } from "node:util";
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
  const { values: { "offline-workflow": adapter, case: caseId } } = parseArgs({
    options: { "offline-workflow": { type: "string" }, case: { type: "string" } },
  });
  assert.ok(adapter !== "" && caseId !== "", "Supply a workflow path or case ID after its option.");
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
  let interrupted = false;
  test.after(() => {
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
      const executeLocal = async (_label, callback) => {
        t.signal.throwIfAborted();
        return callback();
      };
      let attemptedFetch = false;
      const context = {
        caseId: item.id, repetition, sessionId: randomUUID(), signal: t.signal,
        step: executeLocal, tool: executeLocal,
        fetch: async () => {
          attemptedFetch = true;
          throw new Error("Provider transport is disabled for this offline check.");
        },
      };
      const onAbort = () => { interrupted = true; };
      t.signal.addEventListener("abort", onAbort, { once: true });
      try {
        const output = await selected.candidate(structuredClone(item.input), context);
        assert.ok(!attemptedFetch, "Provider transport is disabled for this offline check.");
        const scores = await selected.evaluate({ input: item.input, output, variant: "candidate" });
        const failed = [];
        for (const check of selected.checks) {
          const score = scores[check.id];
          if ((typeof score === "function" ? await score() : score) !== true) failed.push(check.label || check.id);
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

// Offline starter. Replace planCalls with your own local function returning call IDs.
import assert from "node:assert/strict";
import { appendFileSync, closeSync, mkdtempSync, openSync, realpathSync, statSync as workflowFileStat } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { inspect, isDeepStrictEqual, parseArgs, types } from "node:util";
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
  // Score receipts travel over a private IPC pipe. App and test reporter output stays in the child.
  const pairArm = process.send && process.argv[2] === "--_paired-arm" ? process.argv.splice(2, 2)[1] : undefined;
  function invalidSelection(message) {
    console.error(message);
    process.exit(2);
  }
  let values;
  try {
    ({ values } = parseArgs({
      options: {
        "offline-workflow": { type: "string", multiple: true }, case: { type: "string", multiple: true },
        "compare-baseline": { type: "boolean" },
        "require-case": { type: "string", multiple: true }, "require-check": { type: "string", multiple: true },
        "inspect-values": { type: "boolean" }, "inspect-tools": { type: "boolean" }, "require-hosted-labels": { type: "boolean" },
      },
    }));
  } catch {
    invalidSelection("Check your options. Use --offline-workflow PATH and repeat --case=ID to select cases.");
  }
  const { "offline-workflow": paths = [], case: caseIds = [], "inspect-values": inspectValues, "inspect-tools": inspectTools, "require-hosted-labels": requireHostedLabels } = values;
  if (paths.length > 1) invalidSelection("Use --offline-workflow once. Run each workflow in a separate command.");
  const [adapter] = paths;
  const requiredCases = values["require-case"] ?? [], requiredChecks = values["require-check"] ?? [];
  if (requiredCases.length || requiredChecks.length) {
    if (!pairArm && !values["compare-baseline"]) invalidSelection("Use --require-case and --require-check with --compare-baseline.");
    if ([requiredCases, requiredChecks].some(ids => new Set(ids).size !== ids.length || ids.some(id => !id.trim() || /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u.test(id)))) {
      invalidSelection("Provide each required case or check ID once, with a nonempty, single-line ID.");
    }
    const excluded = requiredCases.findIndex(id => caseIds.length && !caseIds.includes(id));
    if (excluded >= 0) invalidSelection("Required case " + pairedId(requiredCases[excluded], excluded) + " is outside --case selection. Remove --case to run the full suite or include that case.");
  }
  if (values["compare-baseline"]) {
    if (!adapter || inspectTools || inspectValues || requireHostedLabels || pairArm) {
      invalidSelection("Use --compare-baseline with --offline-workflow PATH and optional --case IDs. Run inspection or hosted label checks separately.");
    }
    process.exit(await compareLocalArms(adapter, caseIds, requiredCases, requiredChecks));
  }
  if (adapter === "" || caseIds.includes("")) invalidSelection("Supply a workflow path or case ID after its option.");
  if (new Set(caseIds).size !== caseIds.length) invalidSelection("A --case ID was requested more than once. List each ID once.");
  if ((inspectTools || inspectValues) && caseIds.length !== 1) {
    invalidSelection((inspectTools ? "--inspect-tools" : "--inspect-values") + " requires --case CASE_ID. Select exactly one case to inspect.");
  }
  function setupError(message) {
    invalidSelection("No cases ran. " + message);
  }
  // Import only the trusted workflow explicitly selected by the developer.
  let selected = workflow;
  const importStarted = pairArm ? Date.now() : undefined;
  if (adapter) {
    let file;
    try {
      file = workflowFileStat(adapter, { throwIfNoEntry: false });
    } catch {
      setupError("Cannot read the workflow file. Check its path and read permissions, then rerun.");
    }
    if (!file) {
      setupError("Workflow file not found. From your service directory, pass the file path to --offline-workflow.\n"
        + "For a new workflow, download and adapt this example: https://github.com/giolux1235/tokensand-agent-pack/releases/download/workflow-check-0.1.25/observe.workflow.mjs\n"
        + "Codex or Claude Code: Create observe.workflow.mjs around my changed function with expected results and a zero provider budget. Run it locally.");
    }
    if (!file.isFile()) setupError("Select a workflow module file with --offline-workflow.");
    try {
      selected = (await import(pathToFileURL(adapter).href)).default;
    } catch (error) {
      const code = error && typeof error === "object" && !types.isProxy(error)
        ? Object.getOwnPropertyDescriptor(error, "code")?.value : undefined;
      if (code === "ENOENT" && typeof reportWatchPaths === "function") {
        // Top-level reads may fail before the module exports its declarations.
        reportWatchPaths(process.cwd(), [Object.getOwnPropertyDescriptor(error, "path")?.value]);
      }
      if (["ERR_MODULE_NOT_FOUND", "MODULE_NOT_FOUND", "ERR_PACKAGE_PATH_NOT_EXPORTED",
        "ERR_PACKAGE_IMPORT_NOT_DEFINED", "ERR_UNSUPPORTED_DIR_IMPORT"].includes(code)) {
        setupError("A workflow import could not be resolved. Restore the service dependencies or correct its import paths, then rerun.");
      }
      setupError("The workflow could not initialize. Check its module syntax and top-level setup, then rerun.");
    }
  }
  if (!selected || typeof selected !== "object") setupError("Provide a workflow object as the module's default export.");
  if (pairArm) {
    process.on("uncaughtExceptionMonitor", () => process.send({ type: "fatal" }));
    process.on("unhandledRejection", () => process.send({ type: "fatal" }));
    if (!["baseline", "candidate"].includes(pairArm) || ["baseline", "candidate"].some(arm =>
      selected.maxCallsPerRun?.[arm] !== 0 || typeof selected[arm] !== "function") || typeof selected.evaluate !== "function") {
      invalidSelection("Both callbacks, one evaluator and two zero provider budgets are required.");
    }
    if (Array.isArray(selected.cases) && Array.isArray(selected.checks)) {
      const missing = (ids, entries) => ids.filter(id => !entries.some(entry => entry?.id === id));
      const cases = missing([...new Set([...caseIds, ...requiredCases])], selected.cases);
      const checks = missing(requiredChecks, selected.checks);
      if (cases.length || checks.length) {
        await new Promise(resolve => process.send({ type: "missing-coverage", cases, checks }, resolve));
        process.exit(2);
      }
    }
    if (![selected.cases, selected.checks].every(entries => Array.isArray(entries) && entries.length &&
      entries.every(entry => typeof entry?.id === "string" && entry.id.length) &&
      new Set(entries.map(entry => entry.id)).size === entries.length)) invalidSelection("Declare unique case and check IDs.");
    if (selected.timeoutMs !== undefined && (!Number.isSafeInteger(selected.timeoutMs) || selected.timeoutMs < 1 || selected.timeoutMs > 2147480000)) {
      invalidSelection("Use a positive integer trial deadline.");
    }
    selected = { ...selected, timeoutMs: selected.timeoutMs ?? 30000,
      cases: selected.cases.map(({ id, input }) => ({ id, input: structuredClone(input) })),
      checks: selected.checks.map(({ id }) => ({ id })) };
  }
  if (adapter && typeof reportWatchPaths === "function") {
    const { dirname } = await import("node:path");
    reportWatchPaths(dirname(adapter), selected.sourceFiles);
    reportWatchPaths(dirname(adapter), selected.evaluatorSourceFiles);
  }
  if (selected.maxCallsPerRun?.candidate !== 0) setupError("Set the offline candidate provider budget to zero.");
  if (!Array.isArray(selected.cases) || !selected.cases.length) setupError("Provide at least one case.");
  if (!Array.isArray(selected.checks) || !selected.checks.length) setupError("Provide at least one named check.");
  const { isAbsolute, relative, resolve, sep } = await import("node:path");
  // A cached npx runner might have no bin in the app. Use the very file that
  // ran, and resolve the selected adapter against the caller's current directory.
  function commandPath(path) {
    const absolute = resolve(path);
    const local = relative(process.cwd(), absolute);
    return local && local !== ".." && !local.startsWith(".." + sep) && !isAbsolute(local) ? "." + sep + local : absolute;
  }
  function shellWord(value) {
    if (typeof value !== "string" || value.length > 2048 || /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u.test(value)) return null;
    if (/^[A-Za-z0-9_./:@=,+-]+$/.test(value)) return value;
    if (process.platform === "win32") {
      // Avoid cmd/PowerShell expansion and embedded quotes. A Windows path may contain spaces.
      return /^[A-Za-z0-9_./:@=,+ \\-]+$/.test(value) && !value.endsWith("\\") ? '"' + value + '"' : null;
    }
    return "'" + value.replaceAll("'", "'\\''") + "'";
  }
  let firstReplayId;
  let showedAdditionalReplay = false;
  function showCaseReplay(id, interrupted = false) {
    if (caseIds.length === 1 || typeof id !== "string" || !id || firstReplayId === id) return;
    // A later timeout needs its own replay even if an earlier check already failed.
    if (firstReplayId === undefined) {
      firstReplayId = id;
    } else if (interrupted && !showedAdditionalReplay) {
      showedAdditionalReplay = true;
    } else {
      return;
    }
    const script = process.argv[1];
    const args = ["node", commandPath(script)];
    if (adapter) args.push("--offline-workflow", commandPath(adapter));
    args.push("--case=" + id, "--inspect-tools");
    const words = args.map(shellWord);
    if (words.includes(null)) {
      console.log("Replay this case with its exact ID from the workflow. Its ID or file path cannot be safely printed as a shell command.");
      return;
    }
    console.log("Replay this case: " + words.join(" "));
  }
  const focused = caseIds.length > 0;
  const items = focused ? caseIds.map(id => {
    const matches = selected.cases.filter(item => item.id === id);
    if (matches.length !== 1) {
      const choices = selected.cases.map(item => item.id)
        .filter(id => typeof id === "string" && /^[A-Za-z0-9_.:/ -]{1,100}$/.test(id)).slice(0, 8);
      const label = /^[A-Za-z0-9_.:/ -]{1,100}$/.test(id) ? " " + JSON.stringify(id) : "";
      const reason = matches.length ? "The requested case ID" + label + " is duplicated. Give each case a unique ID." : "Unknown case ID" + label + ". Use an exact ID from the selected workflow.";
      invalidSelection(reason + (choices.length ? " Available IDs include " + choices.map(id => JSON.stringify(id)).join(", ") + "." : ""));
    }
    return matches[0];
  }) : selected.cases;
  const definedTrials = Object.hasOwn(selected, "repetitions") ? selected.repetitions : 1;
  if (!Number.isInteger(definedTrials) || definedTrials < 1 || definedTrials > 10) {
    invalidSelection("Set workflow repetitions to a whole number from 1 to 10.");
  }
  const trials = focused ? Math.max(3, definedTrials) : definedTrials;
  let readCriteria, initialCriteria;
  if (pairArm) {
    const declared = selected.evaluatorSourceFiles;
    const paths = Array.isArray(declared) ? [...declared] : declared;
    readCriteria = () => declaredCriteriaDigest(adapter, paths, importStarted);
    try {
      initialCriteria = await readCriteria();
    } catch {
      await new Promise(resolve => process.send({ type: "criteria-unavailable" }, resolve));
      process.exit(2);
    }
    const { serialize } = await import("node:v8");
    const { createHash } = await import("node:crypto");
    process.send({ type: "matrix", cases: items.map(({ id, input }) => [id, createHash("sha256").update(serialize(input)).digest("hex")]),
      checks: selected.checks.map(c => c.id), trials, timeoutMs: selected.timeoutMs,
      declaredCriteria: initialCriteria, evaluator: createHash("sha256").update(Function.prototype.toString.call(selected.evaluate)).digest("hex"),
      checksVersion: selected.checksVersion });
  }
  let sdkInspector;
  if (inspectTools) {
    try {
      const { createTrialToolInspector } = await import("./trace_ai_sdk_7.mjs?trial");
      sdkInspector = await createTrialToolInspector();
    } catch (error) {
      // Older single-file copies can still inspect explicitly wrapped callbacks.
      if (error.code !== "ERR_MODULE_NOT_FOUND") console.error("AI SDK tool inspection is unavailable for this replay.");
    }
  }
  if (focused) {
    const scope = caseIds.length === 1 ? "only " + JSON.stringify(caseIds[0]) : caseIds.length + " selected cases";
    console.log(`Checking ${scope} for ${trials} trials${caseIds.length > 1 ? " each" : ""}. Run the full suite before sharing a result.`);
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
    return valid;
  }
  let interrupted = false;
  if (pairArm) test.after(async () => {
    try {
      if (await readCriteria() !== initialCriteria) process.send({ type: "criteria-unavailable" });
    } catch { process.send({ type: "criteria-unavailable" }); }
    process.send({ type: "done" });
  });
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
    const label = trials > 1 ? `${item.id} (trial ${repetition + 1}/${trials})` : item.id;
    // The private receipt reporter groups complete trials by exact case ID.
    const name = process.env.TOKENSAND_STARTER_COUNTS
      ? JSON.stringify([item.id, repetition + 1, trials]) : label;
    test(name, { timeout: selected.timeoutMs }, async (t) => {
      if (pairArm) process.send({ type: "start", id: item.id, repetition });
      const timings = inspectTools ? focusedToolTimings(repetition + 1) : null;
      const sdkTrial = sdkInspector?.startTrial(repetition + 1);
      let inspectionClosed = false;
      function finishInspection(end) {
        if (inspectionClosed) return;
        inspectionClosed = true;
        const sdk = sdkTrial?.close();
        timings?.show(end, sdk?.available === true);
        if (sdk?.available) showSdkToolCalls(repetition + 1, sdk);
      }
      const executeLocal = async (kind, label, callback) => {
        t.signal.throwIfAborted();
        const validLabel = checkLabel(kind, label);
        if (timings && kind === "tool") return timings.run(validLabel ? label : "[unnamed]", callback);
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
      const onAbort = () => {
        interrupted = true;
        showCaseReplay(item.id, true);
        finishInspection("case interrupted");
      };
      t.signal.addEventListener("abort", onAbort, { once: true });
      const started = performance.now();
      try {
        const input = structuredClone(item.input);
        const output = sdkTrial
          ? await sdkTrial.run(() => selected.candidate(input, context))
          : await selected[pairArm || "candidate"](input, context);
        assert.ok(!attemptedFetch, "Provider transport is disabled for this offline check.");
        if (valuesFile) {
          t.signal.throwIfAborted();
          const row = { trial: repetition + 1, actual: preview(output) };
          if (item.input != null && Object.hasOwn(item.input, "expected")) row.expected = preview(item.input.expected);
          appendFileSync(valuesFile, JSON.stringify(row) + "\n");
        }
        let scores, evaluatorThrew = false;
        try {
          scores = await selected.evaluate({ input: structuredClone(item.input), output, variant: pairArm || "candidate" });
        } catch {
          evaluatorThrew = true;
        }
        const failed = [], unscored = [], scorersThatThrew = [];
        const pairScores = pairArm ? Array(selected.checks.length).fill(null) : null;
        for (const [index, check] of selected.checks.entries()) {
          t.signal.throwIfAborted();
          const label = check.label || check.id;
          try {
            const score = scores && Object.hasOwn(scores, check.id) ? scores[check.id] : undefined;
            const value = typeof score === "function" ? await score() : score;
            if (pairScores && typeof value === "boolean") pairScores[index] = value;
            if (value === false) failed.push(label);
            else if (value !== true) unscored.push(label);
          } catch {
            unscored.push(label);
            scorersThatThrew.push(label);
          }
        }
        // A synchronous callback can finish before Node gets a turn to fire its timer.
        const elapsed = performance.now() - started;
        if (Number.isFinite(selected.timeoutMs) && elapsed > selected.timeoutMs) {
          showCaseReplay(item.id, true);
          failed.push(`Case took ${Math.ceil(elapsed)} ms (deadline ${selected.timeoutMs} ms)`);
        } else if (pairArm && !t.signal.aborted) {
          process.send({ type: "scores", id: item.id, repetition, scores: pairScores });
        }
        if (unscored.length) {
          const reasons = failed.length ? [`Failed checks: ${failed.join(", ")}`] : [];
          reasons.push(`Unscored checks: ${unscored.join(", ")}`);
          if (evaluatorThrew) reasons.push("The evaluator threw before returning scores");
          if (scorersThatThrew.length) reasons.push(`Scorers that threw: ${scorersThatThrew.join(", ")}`);
          const error = new Error(reasons.join(". "));
          error.code = failed.length ? "TOKENSAND_FAILED_AND_UNSCORED" : "TOKENSAND_UNSCORED";
          error.scorerError = evaluatorThrew || scorersThatThrew.length > 0;
          throw error;
        }
        assert.equal(failed.length, 0, failed.join(", "));
      } catch (error) {
        showCaseReplay(item.id);
        throw error;
      } finally {
        finishInspection("case ended");
        t.signal.removeEventListener("abort", onAbort);
      }
    });
  }
  for (const item of items) {
    for (let repetition = 0; repetition < trials; repetition++) registerCase(item, repetition);
  }
}

// Only used on an explicitly inspected local replay. Keep active calls and the
// five slowest completed calls. A case timeout can fire before a tool settles.
function focusedToolTimings(trial) {
  const active = new Set();
  const completed = [];
  let total = 0;
  let shown = false;
  let firstRejection;
  return {
    async run(label, callback) {
      const call = { index: ++total, label, started: performance.now() };
      active.add(call);
      let outcome = "rejected";
      try {
        const result = await callback();
        outcome = "returned";
        return result;
      } finally {
        active.delete(call);
        const row = { ...call, outcome, ms: performance.now() - call.started };
        if (outcome === "rejected") firstRejection ??= row;
        completed.push(row);
        completed.sort((a, b) => b.ms - a.ms || a.index - b.index);
        if (completed.length > 5) completed.pop();
      }
    },
    show(end, hasSdk) {
      if (shown) return;
      shown = true;
      if (!total) {
        if (!hasSdk) console.log(`No ctx.tool callbacks recorded in trial ${trial}.`);
        return;
      }
      const now = performance.now();
      const rows = [];
      for (const call of active) {
        rows.push({ ...call, outcome: "still running", ms: now - call.started });
        if (rows.length === 5) break;
      }
      rows.push(...completed);
      if (firstRejection && !completed.includes(firstRejection)) rows.push(firstRejection);
      rows.sort((a, b) => a.index - b.index);
      console.log(`Local tool calls (trial ${trial}, ${end}): ${rows.length} of ${total} ctx.tool callbacks`);
      for (const row of rows) {
        console.log(`  #${row.index} ${row.label} — ${row.outcome}, ${Math.round(row.ms)} ms`);
      }
    },
  };
}

function showSdkToolCalls(trial, inspection) {
  const { tools, observed, omitted, comparison } = inspection;
  console.log(`AI SDK tool calls (trial ${trial}): ${observed} recorded`);
  // Surface errors and changes first. A conversion may be intentional.
  const ordered = [...tools].sort((a, b) => b.errors - a.errors || b.changed - a.changed || b.completed - a.completed);
  for (const row of ordered.slice(0, 5)) {
    const details = [`${row.completed - row.errors} returned`];
    if (row.errors) details.push(`${row.errors} errors`);
    if (comparison) {
      const paired = row.changed + row.equal;
      if (paired) details.push(`selected value changed in ${row.changed}/${paired} tool-to-model pairs`);
      if (row.unavailable) details.push(`${row.unavailable} without a matched pair`);
    }
    console.log(`  ${row.tool} — ${details.join(", ")}`);
  }
  if (ordered.length > 5) console.log(`  ${ordered.length - 5} additional tool names in the private trace.`);
  if (omitted) console.log("Some tool events were incomplete, ambiguous or over the capture limit.");
}

async function declaredCriteriaDigest(adapter, paths = [], importStarted) {
  if (!Array.isArray(paths)) throw new Error();
  if (!paths.length) return "";
  const { createHash } = await import("node:crypto");
  const { constants, open, realpath } = await import("node:fs/promises");
  const { dirname, isAbsolute, relative, resolve, sep } = await import("node:path");
  const root = dirname(await realpath(adapter));
  const digest = createHash("sha256");
  for (const path of paths) {
    if (typeof path !== "string" || !path || isAbsolute(path)) throw new Error();
    const resolved = await realpath(resolve(root, path));
    const local = relative(root, resolved);
    if (!local || isAbsolute(local) || local.split(sep).includes("..")) throw new Error();
    const file = await open(resolved, constants.O_RDONLY | constants.O_NONBLOCK | constants.O_NOFOLLOW);
    try {
      const initial = await file.stat();
      // A module can import an old value and change its source during initialization,
      // before we learn the declared file list. Hold on writes in that window.
      if (!initial.isFile() || initial.ctimeMs >= importStarted) throw new Error();
      const hash = createHash("sha256");
      for await (const chunk of file.createReadStream({ autoClose: false })) hash.update(chunk);
      if ((await file.stat()).ctimeMs !== initial.ctimeMs) throw new Error();
      digest.update(JSON.stringify([path, local, hash.digest("hex")]));
    } finally { await file.close(); }
  }
  return digest.digest("hex");
}

function pairedId(id, index) {
  return id.length <= 120 && !/[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u.test(id) &&
    !/(?:^|[^a-z0-9])(?:sk[-_]|tobs_|dai_|tvly-)/i.test(id) ? JSON.stringify(id) : "#" + (index + 1);
}

async function runLocalArm(adapter, ids, requiredCases, requiredChecks, arm) {
  const { fork } = await import("node:child_process");
  const options = [...ids.map(id => "--case=" + id), ...requiredCases.map(id => "--require-case=" + id),
    ...requiredChecks.map(id => "--require-check=" + id)];
  const child = fork(process.argv[1], ["--_paired-arm", arm, "--offline-workflow", adapter, ...options], {
    execArgv: [], stdio: ["ignore", "ignore", "ignore", "ipc"],
  });
  return new Promise(resolve => {
    let matrix, timer, timedOut = false, finished = false, active, invalid = false, criteriaUnavailable = false;
    const scores = new Map();
    let missingCoverage;
    function deadline(ms) {
      clearTimeout(timer);
      timer = setTimeout(() => { timedOut = true; child.kill("SIGKILL"); }, ms);
    }
    deadline(30000);
    child.on("message", msg => {
      if (timedOut || !msg || typeof msg !== "object") return;
      if (msg.type === "matrix" && !matrix) {
        matrix = msg;
        deadline(30000);
      } else if (matrix && msg.type === "start") {
        active = JSON.stringify([msg.id, msg.repetition]);
        deadline(matrix.timeoutMs + 1000); // Covers a CPU-bound callback that never yields to Node's timer.
      } else if (matrix && msg.type === "scores" && Array.isArray(msg.scores) &&
          msg.scores.length === matrix.checks.length && msg.scores.every(s => s === null || typeof s === "boolean")) {
        const key = JSON.stringify([msg.id, msg.repetition]);
        if (active !== key || scores.has(key)) invalid = true;
        else scores.set(key, msg.scores);
      } else if (msg.type === "missing-coverage" && !matrix && Array.isArray(msg.cases) && Array.isArray(msg.checks)) {
        // Only echo the caller's requirements, never a worker-supplied label.
        missingCoverage = {
          cases: [...new Set([...ids, ...requiredCases])].filter(id => msg.cases.includes(id)),
          checks: requiredChecks.filter(id => msg.checks.includes(id)),
        };
      } else if (msg.type === "criteria-unavailable") {
        criteriaUnavailable = true;
      } else if (msg.type === "done") {
        finished = true;
        deadline(2000);
      } else {
        invalid = true;
      }
    });
    child.on("error", () => { clearTimeout(timer); resolve(null); });
    child.on("close", code => {
      clearTimeout(timer);
      // A nonzero exit with a complete all-green matrix is a runtime failure.
      const reportedFailure = matrix && (scores.size < matrix.cases.length * matrix.trials ||
        [...scores.values()].some(values => values.some(value => value !== true)));
      const normal = finished && (code === 0 || code === 1 && reportedFailure);
      if (missingCoverage && code === 2 && !matrix && !invalid && (missingCoverage.cases.length || missingCoverage.checks.length)) {
        resolve({ missingCoverage });
      } else if (criteriaUnavailable || (timedOut && !finished && matrix?.declaredCriteria)) {
        resolve({ criteriaUnavailable: true });
      } else if (!matrix || invalid || !(normal || timedOut && active && !finished)) {
        resolve(null);
      } else {
        resolve({ matrix, scores });
      }
    });
  });
}

async function compareLocalArms(adapter, ids, requiredCases, requiredChecks) {
  const { createHash } = await import("node:crypto");
  const { readFile } = await import("node:fs/promises");
  const hash = async () => createHash("sha256").update(await readFile(adapter)).digest("hex");
  let before, after;
  try {
    const original = await hash();
    for (const arm of ["baseline", "candidate"]) {
      const result = await runLocalArm(adapter, ids, requiredCases, requiredChecks, arm);
      if (result?.missingCoverage) {
        for (const [kind, missing] of Object.entries(result.missingCoverage)) {
          if (missing.length) console.error("Missing required " + kind + ": " + missing.map(pairedId).join(", "));
        }
        console.error("Restore the missing coverage in the workflow and rerun with the same requirements.");
        return 2;
      }
      if (result?.criteriaUnavailable) {
        console.error("Declared evaluator files changed or could not be verified. Check evaluatorSourceFiles in the service directory, keep the criteria fixed and rerun.");
        return 2;
      }
      if (!result) {
        console.error(`Could not score the ${arm}. Check the workflow path, selected IDs, both callbacks, evaluator and zero provider budgets. Run the ordinary local check for setup details.`);
        return 2;
      }
      if (await hash() !== original) {
        console.error("The workflow adapter changed during the check. Save it and rerun.");
        return 2;
      }
      if (arm === "baseline") before = result;
      else after = result;
    }
  } catch {
    console.error("Could not read the workflow or start a local worker. Check the --offline-workflow file path and Node subprocess access.");
    return 2;
  }
  if (!isDeepStrictEqual(before.matrix, after.matrix)) {
    console.error("The cases, inputs, repetitions, deadlines or evaluator changed between variants. Keep the declared fixtures and evaluator files fixed and rerun.");
    return 2;
  }
  const {cases, checks, trials} = before.matrix;
  let failed = false, incomplete = false, newFailure = false;
  console.log("Baseline and candidate on the same cases and checks");
  for (const [ci, [id]] of cases.entries()) {
    for (const [index, check] of checks.entries()) {
      const groups = new Map();
      for (let trial = 0; trial < trials; trial++) {
        const key = JSON.stringify([id, trial]);
        const old = before.scores.get(key)?.[index];
        const current = after.scores.get(key)?.[index];
        let transition;
        if (old == null || current == null) {
          const state = score => score == null ? "unscored" : score ? "passed" : "failed";
          transition = `Unscored (baseline ${state(old)}, candidate ${state(current)})`;
          incomplete = true;
        } else if (!current) {
          transition = old ? "New failure" : "Existing failure";
          newFailure ||= old;
          failed = true;
        } else {
          transition = old ? "Passed both" : "Recovered";
        }
        if (!groups.has(transition)) groups.set(transition, []);
        groups.get(transition).push(trial + 1);
      }
      for (const [transition, numbers] of groups) {
        console.log(`${transition}: ${pairedId(id, ci)} / ${pairedId(check, index)} · trials ${numbers.join(", ")}`);
      }
    }
  }
  if (!newFailure) console.log(incomplete ? "New failures: undetermined (unscored checks)" : "New failures: 0");
  return failed || incomplete ? 1 : 0;
}

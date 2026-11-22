// Save beside your trusted observe.workflow.mjs. Reads private local plans and emits one case only.
import { createHash, createHmac } from "node:crypto";
import { execFile, execFileSync, fork, spawnSync } from "node:child_process";
import { constants } from "node:fs";
import { open, readFile, realpath } from "node:fs/promises";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";

const hash = /^[a-f0-9]{64}$/;
const commit = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const caseIdPattern = /^[A-Za-z0-9_-]{1,80}$/;
const sha = value => createHash("sha256").update(value).digest("hex");
class ReviewError extends Error {}
function requireReview(value, message = "Invalid local evidence. Choose the original local run directories.") {
  if (!value) throw new ReviewError(message);
}

async function readJson(path, budget) {
  const handle = await open(path, constants.O_RDONLY | constants.O_NONBLOCK | constants.O_NOFOLLOW);
  try {
    const info = await handle.stat();
    requireReview(info.isFile() && info.size <= 8 * 1024 * 1024);
    if (budget) {
      budget.remaining -= info.size;
      requireReview(budget.remaining >= 0, "The selected saved trials exceed the 16 MiB review limit.");
    }
    return JSON.parse(await handle.readFile("utf8"));
  } finally { await handle.close(); }
}

async function readRun(dir, id, canonical, validate) {
  const [frozen, result] = await Promise.all(["plan.json", "result.json"].map(file => readJson(join(dir, file))));
  const { plan } = frozen;
  validate(plan);
  requireReview(frozen.schemaVersion === 1 && result.schemaVersion === 1 && result.kind === "local-workflow-comparison"
    && typeof frozen.id === "string" && result.id === frozen.id && canonical(result.plan) === canonical(plan)
    && ["complete", "interrupted"].includes(result.state) && plan.maxCallsPerRun.baseline === 0 && plan.maxCallsPerRun.candidate === 0);
  requireReview(commit.test(plan.sourceGitCommit), "Both runs need captureGitCommit: true and committed workflow sources.");
  requireReview(typeof frozen.privateInputKey === "string" && hash.test(frozen.privateInputKey));
  const checkIds = plan.checks.map(check => check.id);
  const selected = plan.cases.find(item => item.id === id);
  requireReview(selected, "The selected case must be present in both plans.");
  const expected = plan.repetitions * plan.cases.length * 2;
  requireReview(Array.isArray(result.runs) && result.runs.length <= expected && (result.state !== "complete" || result.runs.length === expected));
  const seen = new Set(), rows = [], outputFiles = [];
  for (const [index, run] of result.runs.entries()) {
    requireReview(plan.cases.some(c => c.id === run.caseId) && ["baseline", "candidate"].includes(run.variant)
      && Number.isInteger(run.repetition) && run.repetition >= 0 && run.repetition < plan.repetitions);
    requireReview(["ok", "check_error", "timeout", "workflow_error", "telemetry_failed", "budget_exceeded"].includes(run.status));
    requireReview(run.checks && Object.keys(run.checks).length === checkIds.length && checkIds.every(key =>
      Object.hasOwn(run.checks, key) && (typeof run.checks[key] === "boolean" || (run.status !== "ok" && run.checks[key] === null))));
    const key = [run.caseId, run.variant, run.repetition].join("/");
    requireReview(!seen.has(key)); seen.add(key);
    if (run.caseId === id) {
      rows.push(run);
      outputFiles.push(join(dir, "run-" + String(index + 1).padStart(3, "0") + ".json"));
    }
  }
  // Source paths are metadata. Never load a module named by an evidence file.
  const sources = new Map(frozen.sources.map(src => [src.path, src.sha256]));
  requireReview(sources.size === frozen.sources.length && hash.test(sources.get(frozen.config)) && hash.test(frozen.functionDigests?.evaluator));
  let dependencies;
  if (Array.isArray(frozen.evaluatorSources)) {
    dependencies = frozen.evaluatorSources.map(path => {
      requireReview(hash.test(sources.get(path)));
      return [relative(dirname(frozen.config), path), sources.get(path)];
    }).sort(([a], [b]) => a.localeCompare(b));
  }
  return { frozen, plan, state: result.state, rows, outputFiles, selected, configHash: sources.get(frozen.config), dependencies };
}

// Trusted adapter code runs in a child so its logs and private outputs stay out of the note.
async function adapterWorker(id, cwd = process.cwd(), replay) {
  const child = fork(fileURLToPath(import.meta.url), [replay ? "--score-outputs" : "--read-fixture", id], {
    cwd, stdio: ["ignore", "ignore", "ignore", "ipc"], execArgv: [], serialization: "advanced",
    env: { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, NODE_ENV: process.env.NODE_ENV },
  });
  try {
    return await new Promise((accept, reject) => {
      const fail = () => reject(new ReviewError(replay
        ? "Could not reproduce the original checks. Use the trusted offline scorer and its original dependencies."
        : "Could not load the case from the trusted local observe.workflow.mjs. Check its offline setup."));
      const timeout = setTimeout(fail, replay ? 30000 : 10000);
      child.once("error", () => { clearTimeout(timeout); fail(); });
      child.once("exit", () => { clearTimeout(timeout); fail(); });
      child.once("message", item => { clearTimeout(timeout); accept(item); });
      if (replay) child.send(replay);
    });
  } finally { child.kill("SIGKILL"); }
}

async function savedOutputs(run, canonical, budget) {
  const outputs = { baseline: [], candidate: [] };
  for (const [index, row] of run.rows.entries()) {
    const trial = await readJson(run.outputFiles[index], budget);
    requireReview(trial.kind === "local-workflow-run" && canonical(trial.run) === canonical(row)
      && Object.hasOwn(trial, "output"), "A saved trial is missing its output or does not match the result.");
    outputs[row.variant][row.repetition] = trial.output;
  }
  return outputs;
}

function gitAt(cwd, ...args) {
  return execFileSync("git", ["--no-replace-objects", ...args], {cwd, encoding:"utf8", stdio:["ignore","pipe","ignore"]}).trim();
}

const adapterFile = "observe.workflow.mjs";
const repoTest = "test/tokensand-workflow.test.mjs";
const serviceKey = adapterPath => sha(adapterPath).slice(0, 20);

// Read file names from Git without quoting. The shared note omits repository paths.
function trackedService(root, revision, adapter) {
  const testPath = adapter.slice(0, -adapterFile.length) + repoTest;
  const listing = execFileSync("git", ["--no-replace-objects", "--literal-pathspecs", "ls-tree", "-rz", "--full-tree", revision, "--", adapter, testPath], {
    cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"],
  });
  const files = new Map();
  for (const entry of listing.split("\0")) {
    const match = /^100(?:644|755) blob ([a-f0-9]+)\t([\s\S]+)$/.exec(entry);
    if (match) files.set(match[2], match[1]);
  }
  if (files.has(adapter) && files.has(testPath)) return files;
}

function noteCommand(root, serviceDir, run, id) {
  const rev = run.plan.sourceGitCommit;
  const adapter = relative(root, join(serviceDir, adapterFile)).split(sep).join("/");
  const files = trackedService(root, rev, adapter);
  if (!files) return "No pinned repo test found for this service at " + rev + ".";
  // The older service may have moved. Do not advertise a command for different source.
  const blob = execFileSync("git", ["--no-replace-objects", "cat-file", "blob", files.get(adapter)], {
    cwd: root, maxBuffer: 8 * 1024 * 1024, stdio: ["ignore", "pipe", "ignore"],
  });
  if (sha(blob) !== run.configHash) return "The committed adapter at this location differs from the saved run.";
  return "node review-repair-v1.mjs recheck " + rev + " " + serviceKey(adapter) + " --case " + id;
}

async function recheck(args) {
  const [rev, key, flag, id] = args;
  requireReview(args.length === 4 && commit.test(rev) && /^[a-f0-9]{20}$/.test(key)
    && flag === "--case" && caseIdPattern.test(id), "Use the full recheck command from the repair note.");
  // These Git reads are independent. Only the immutable target tree is used to bind executable files.
  const git = promisify(execFile);
  const options = {encoding: "utf8", maxBuffer: 8 * 1024 * 1024};
  const [checkout, status, adapters] = await Promise.all([
    git("git", ["--no-replace-objects", "rev-parse", "--show-toplevel", "HEAD"], options),
    git("git", ["--no-replace-objects", "status", "--porcelain", "--untracked-files=no"], options),
    git("git", ["ls-files", "--full-name", "-z", "--", ":(top,glob)**/observe.workflow.mjs"], options),
  ]);
  const identity = checkout.stdout.trimEnd();
  const separator = identity.lastIndexOf("\n");
  const root = identity.slice(0, separator);
  requireReview(separator > 0 && identity.slice(separator + 1) === rev,
    "No test ran. Open a checkout of " + rev + ". To create one: git worktree add --detach ../tokensand-review " + rev);
  requireReview(status.stdout === "", "No test ran. Use a clean checkout of the named revision.");
  const paths = adapters.stdout;
  const matches = paths.split("\0").filter(path => path && serviceKey(path) === key);
  requireReview(matches.length === 1, "No test ran. The service in the note needs one matching committed adapter and repo test.");
  const files = trackedService(root, rev, matches[0]);
  requireReview(files, "No test ran. This revision needs a regular committed adapter and repo test.");
  const directory = dirname(join(root, matches[0]));
  const relativePaths = [...files.keys()];
  const pathsAreRegular = await Promise.all(relativePaths.map(async file => {
    const path = join(root, file);
    return await realpath(path) === path;
  }));
  requireReview(pathsAreRegular.every(Boolean), "No test ran. Restore the service’s regular tracked files.");
  // Let Git apply the checkout's configured clean filters, including line-ending conversion.
  const blobs = gitAt(root, "hash-object", "--", ...relativePaths).split("\n");
  requireReview(blobs.length === relativePaths.length && relativePaths.every((path, i) => blobs[i] === files.get(path)),
    "No test ran. Restore the adapter and repo test from the named revision.");
  const env = {...process.env};
  delete env.TOKENSAND_SUITE_EVIDENCE;
  const child = spawnSync(process.execPath, [repoTest, "--case", id], {
    cwd: directory, env, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], maxBuffer: 4 * 1024 * 1024,
  });
  process.stdout.write(child.stdout ?? "");
  process.stderr.write(child.stderr ?? "");
  requireReview(Number.isInteger(child.status), "The service test did not finish. Check the local Node setup.");
  requireReview(child.stdout.includes("Focused replay: " + id + " (1 of "),
    "No focused result. Install the pinned SDK and use a Tokens& repo test that supports --case.");
  if (child.status === 0) {
    const totals = /\b(\d+)\/(\d+) focused trials\b/.exec(child.stdout);
    requireReview(totals && Number(totals[1]) >= 6 && totals[1] === totals[2],
      "No complete focused result. Check the trial counts before accepting this change.");
  }
  return child.status;
}

async function replayOriginal(cwd, earlier, later, id, canonical, workflowsUrl) {
  const root = await realpath(cwd);
  requireReview(gitAt(root, "rev-parse", "HEAD") === earlier.plan.sourceGitCommit
    && gitAt(root, "status", "--porcelain", "--untracked-files=no") === "", "Choose the clean, trusted service checkout of the earlier revision.");
  requireReview(earlier.dependencies && sha(await readFile(join(root, "observe.workflow.mjs"))) === earlier.configHash,
    "The original scorer and its declared files must match the earlier plan.");
  // Only follow paths declared by the user's trusted adapter. Evidence cannot nominate code to execute.
  const budget = {remaining: 16 * 1024 * 1024};
  const response = await adapterWorker(id, root, {
    workflowsUrl,
    definition: {workflow:earlier.plan.workflow, checks:earlier.plan.checks, evaluator:earlier.frozen.functionDigests.evaluator,
      dependencies:earlier.dependencies, repetitions:earlier.plan.repetitions, timeoutMs:earlier.plan.timeoutMs},
    before: await savedOutputs(earlier, canonical, budget), after: await savedOutputs(later, canonical, budget),
  });
  const digest = createHmac("sha256", earlier.frozen.privateInputKey).update(canonical(response.input)).digest("hex");
  requireReview(digest === earlier.selected.inputSha256, "The original checkout loaded a different case input.");
  const indexed = rows => new Map(rows.map(row => [row.variant + "/" + row.repetition, row]));
  const original = indexed(response.before);
  requireReview(earlier.rows.every(row => canonical(original.get(row.variant + "/" + row.repetition)?.checks) === canonical(row.checks)),
    "The original scorer did not reproduce the earlier scores from saved JSON outputs.");
  return describe({plan:earlier.plan, state:"complete", rows:response.after});
}

// Reuse the SDK's real scoring semantics, including deferred scorers and per-trial timeouts.
// Only the supplied saved values are returned by these replacement task functions.
async function scoreOutputs(w, selected, data) {
  const {runLocalWorkflowExperiment} = await import(data.workflowsUrl);
  const {definition: spec} = data;
  requireReview(w.id === spec.workflow && sha(w.evaluate.toString()) === spec.evaluator
    && Array.isArray(w.evaluatorSourceFiles) && w.evaluatorSourceFiles.length === spec.dependencies.length);
  const expected = new Map(spec.dependencies);
  for (const file of w.evaluatorSourceFiles) {
    const path = await realpath(resolve(file));
    requireReview(expected.get(relative(process.cwd(), path)) === sha(await readFile(path)));
  }
  async function scoreBatch(outputs) {
    const result = await runLocalWorkflowExperiment({definition:{
      cases:[selected], checks:spec.checks, repetitions:spec.repetitions, timeoutMs:spec.timeoutMs,
      maxCallsPerRun:{baseline:0,candidate:0}, evaluate:w.evaluate.bind(w),
      baseline:(_, ctx) => outputs.baseline[ctx.repetition],
      candidate:(_, ctx) => outputs.candidate[ctx.repetition],
    }});
    requireReview(result.state === "complete" && result.runs.length === spec.repetitions * 2 && result.runs.every(r => r.status === "ok"));
    return result.runs.map(({variant,repetition,status,checks}) => ({variant,repetition,status,checks}));
  }
  return {input:selected.input, before:await scoreBatch(data.before), after:await scoreBatch(data.after)};
}

function describe(run) {
  const trials = run.rows.filter(r => r.variant === "candidate").sort((a,b) => a.repetition - b.repetition);
  const failed = trials.some(r => ["ok", "check_error"].includes(r.status) && Object.values(r.checks).includes(false));
  const complete = run.state === "complete" && run.rows.length === run.plan.repetitions * 2 && run.rows.every(r => r.status === "ok");
  const baselinePassed = run.rows.filter(r => r.variant === "baseline").every(r => Object.values(r.checks).every(Boolean));
  const lines = run.plan.checks.map(({ id }) => {
    const outcomes = Array.from({length: run.plan.repetitions}, (_, i) => {
      const r = trials.find(t => t.repetition === i);
      if (!r) return "not run";
      if (!["ok", "check_error"].includes(r.status)) return r.status;
      return r.checks[id] === null ? "unscored" : r.checks[id] ? "pass" : "fail";
    });
    return id + ": " + outcomes.join(", ");
  });
  return { failed, complete, baselinePassed, lines };
}

async function main() {
  const [beforeDir, afterDir, flag, id, ...rest] = process.argv.slice(2);
  requireReview(beforeDir && afterDir && flag === "--case" && caseIdPattern.test(id ?? "") && (rest.length === 0 || (rest.length === 2 && rest[0] === "--original-checkout" && rest[1])),
    "Usage: node review-repair-v1.mjs BEFORE_RUN_DIR AFTER_RUN_DIR --case CASE_ID [--original-checkout TRUSTED_EARLIER_SERVICE]");
  // Resolve the later service's ESM-only SDK, even when the reviewer lives in a shared skill directory.
  let workflowsUrl;
  try {
    workflowsUrl = dirname(fileURLToPath(import.meta.url)) === process.cwd()
      ? import.meta.resolve("@tokensand/observability/workflows")
      : execFileSync(process.execPath, ["--input-type=module", "-e",
          "process.stdout.write(import.meta.resolve('@tokensand/observability/workflows'))"], {
          cwd: process.cwd(), encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "ignore"],
          env: {PATH:process.env.PATH, SystemRoot:process.env.SystemRoot},
        });
  } catch { throw new ReviewError("Run from the later service checkout with its pinned Tokens& Node SDK installed."); }
  const sdk = new URL("../../", workflowsUrl);
  const {canonical} = await import(new URL("cli-gate.mjs", sdk));
  const {validateExperimentPlan} = await import(new URL("cli-plan-validation.mjs", sdk));
  const [earlier, later] = await Promise.all([beforeDir, afterDir].map(dir => readRun(dir, id, canonical, validateExperimentPlan)));
  requireReview(earlier.plan.workflow === later.plan.workflow && earlier.plan.sourceGitCommit !== later.plan.sourceGitCommit,
    "Choose two distinct revisions of the same workflow, earlier result first.");
  const git = (...args) => gitAt(process.cwd(), ...args);
  requireReview(git("rev-parse","HEAD") === later.plan.sourceGitCommit, "Use a clean checkout of the later result’s revision.");
  requireReview(git("status","--porcelain","--untracked-files=no") === "", "Commit or set aside tracked edits before reviewing this revision.");
  try { git("merge-base", "--is-ancestor", earlier.plan.sourceGitCommit, later.plan.sourceGitCommit); }
  catch { throw new ReviewError("Fetch the earlier revision and choose a later commit that descends from it."); }
  const config = resolve("observe.workflow.mjs");
  const handle = await open(config, "r");
  try { requireReview(sha(await handle.readFile()) === later.configHash, "The workflow file differs from the later plan."); }
  finally { await handle.close(); }
  const fixture = await adapterWorker(id);
  requireReview(fixture.workflow === later.plan.workflow && fixture.scorer === later.frozen.functionDigests.evaluator);
  const input = canonical(fixture.input);
  const matches = run => createHmac("sha256", run.frozen.privateInputKey).update(input).digest("hex") === run.selected.inputSha256;
  requireReview(matches(later), "The current case input differs from the later run. Restore its original fixture to review.");
  const warnings = [];
  if (!matches(earlier)) warnings.push("Selected case input changed.");
  if (earlier.frozen.functionDigests.evaluator !== later.frozen.functionDigests.evaluator) {
    warnings.push(rest.length ? "Scorer function changed." : "Scorer function changed. Add --original-checkout TRUSTED_EARLIER_SERVICE to score saved outputs with the earlier checks.");
  }
  if (canonical(earlier.plan.checks) !== canonical(later.plan.checks) || earlier.plan.checksVersion !== later.plan.checksVersion) warnings.push("Check definitions changed.");
  if (!earlier.dependencies || !later.dependencies) warnings.push("Scorer dependencies were not declared in both runs.");
  else if (canonical(earlier.dependencies) !== canonical(later.dependencies)) warnings.push("Declared scorer dependencies changed.");

  if (earlier.plan.scope !== later.plan.scope) warnings.push("Case scope changed.");
  if (earlier.plan.timeoutMs !== later.plan.timeoutMs || earlier.plan.repetitions !== later.plan.repetitions) warnings.push("Trial settings changed.");
  const before = describe(earlier), after = describe(later);
  if (!before.complete || !after.complete) warnings.push("The runs contain incomplete evidence for this review.");
  if (!before.baselinePassed || !after.baselinePassed) warnings.push("A baseline check failed.");
  if (!before.failed) warnings.push("The earlier candidate has no recorded failing check for this case.");
  if (after.failed) warnings.push("A candidate check still fails.");
  let rescored;
  if (rest.length) {
    requireReview(before.complete && after.complete && matches(earlier) && earlier.plan.repetitions === later.plan.repetitions,
      "Original scoring needs complete trials, the same case input and matching repetition counts.");
    rescored = await replayOriginal(rest[1], earlier, later, id, canonical, workflowsUrl);
    if (rescored.failed || !rescored.baselinePassed) warnings.push("Saved outputs fail the original checks.");
  }
  if (earlier.configHash !== later.configHash && !rescored) warnings.push("Workflow definition changed. To check saved outputs, add --original-checkout TRUSTED_EARLIER_SERVICE.");
  const root = gitAt(process.cwd(), "rev-parse", "--show-toplevel");
  const laterDir = await realpath(process.cwd());
  const earlierDir = rest.length ? await realpath(rest[1]) : laterDir;
  const priorRoot = rest.length ? gitAt(earlierDir, "rev-parse", "--show-toplevel") : root;
  // A green later replay uses that revision's scorer and cannot resolve a held review.
  const showLaterReplay = !warnings.length || (after.complete && after.failed);
  const lines = ["Tokens& local repair review (developer-reported)", earlier.plan.workflow + " / " + id,
    ...(warnings.length ? ["Hold for review", ...warnings] : [rescored ? "Selected case recovered under the original checks on saved outputs." : "Selected case recovered with matching input and declared scorer."]),
    "", "Before " + earlier.plan.sourceGitCommit, ...before.lines, "", "After " + later.plan.sourceGitCommit, ...after.lines, "",
    ...(rescored ? ["After, scored with the original checks", ...rescored.lines, ""] : []),
    "Share this note and the public review-repair-v1.mjs file. Keep run directories private.",
    "Save the reviewer at the repo root. Install the pinned SDK and dependencies before running these trusted offline tests. Each revision uses its own scorer.",
    "Earlier checkout:", noteCommand(priorRoot, earlierDir, earlier, id),
    ...(showLaterReplay
      ? ["Later checkout:", noteCommand(root, laterDir, later, id)]
      : ["Resolve the hold above, then run the repair review again before sharing a passing result."])];
  console.log(lines.join("\n"));
  return warnings.length ? 100 : 0;
}

if (process.send && ["--read-fixture", "--score-outputs"].includes(process.argv[2])) {
  try {
    const replay = process.argv[2] === "--score-outputs"
      ? new Promise(accept => process.once("message", accept)) : null;
    const w = (await import(pathToFileURL(resolve("observe.workflow.mjs")))).default;
    const cases = w.cases.filter(c => c.id === process.argv[3]);
    if (cases.length === 1 && w.maxCallsPerRun?.baseline === 0 && w.maxCallsPerRun?.candidate === 0) {
      if (replay) {
        process.send(await scoreOutputs(w, cases[0], await replay));
      } else {
        process.send({workflow:w.id, input:cases[0].input, scorer:sha(w.evaluate.toString())});
      }
    }
  } catch { if (process.connected) process.disconnect(); process.exitCode = 1; }
} else {
  try { process.exitCode = process.argv[2] === "recheck" ? await recheck(process.argv.slice(3)) : await main(); }
  catch (error) {
    console.error(error instanceof ReviewError ? error.message : "Unable to review the local evidence. Check both run directories, the installed SDK and the current checkout.");
    process.exitCode = process.argv[2] === "recheck" ? 2 : 1;
  }
}

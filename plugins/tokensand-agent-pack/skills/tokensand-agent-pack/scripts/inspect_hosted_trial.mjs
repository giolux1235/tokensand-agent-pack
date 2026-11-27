// Read the Node SDK's original local files. Never import an adapter or contact its collector.
import { constants } from "node:fs";
import { open } from "node:fs/promises";
import { join } from "node:path";
import { isDeepStrictEqual, parseArgs } from "node:util";

const usage = "node inspect_hosted_trial.mjs RUN_DIRECTORY --result UUID_OR_PRIVATE_RESULT_URL --case CASE_ID --trial 1 [--field FIELD_OR_POINTER] [--plan PLAN_FILE]";
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const isUuid = value => typeof value === "string" && uuid.test(value);
const caseId = /^[a-z0-9_-]{1,80}$/i;
const digest = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const checkId = /^[a-z][a-z0-9_]{0,79}$/;
const secretName = /auth|token|cookie|secret|password|passcode|passphrase|otp|onetime|recovery|apikey|privatekey|signingkey|accesskey|sessionid|credential/i;
const secretValue = /authorization|credential|password|private.?key|secret|(?:^|[^a-z0-9])(?:sk[-_]|gh[pousr]_|github_pat_|tobs_|dai_|tvly-|eyJ)|\bBearer\s|[A-Za-z0-9_+/=-]{30,}/i;
const statuses = ["ok", "workflow_error", "check_error", "timeout", "telemetry_failed", "budget_exceeded"];
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const duration = value => Number.isFinite(value) && value >= 0;
class EvidenceError extends Error {}
function requireEvidence(condition, message = "Inconsistent saved trial evidence. Use the original SDK run directory.") {
  if (!condition) throw new EvidenceError(message);
}

async function readJson(path, label) {
  try {
    const file = await open(path, constants.O_RDONLY | constants.O_NONBLOCK | constants.O_NOFOLLOW);
    try {
      const stat = await file.stat();
      requireEvidence(stat.isFile() && stat.size <= 8 * 1024 * 1024);
      return JSON.parse(await file.readFile("utf8"));
    } finally { await file.close(); }
  } catch {
    throw new EvidenceError("Could not read " + label + " as a regular JSON file (8 MiB limit).");
  }
}

function resultId(input) {
  if (isUuid(input)) return input.toLowerCase();
  let url;
  try { url = new URL(input); } catch { /* Handled by the same input error below. */ }
  const message = "Use the experiment UUID or its Tokens& private result link.";
  requireEvidence(url?.origin === "https://tokensand.com" && url.pathname === "/dashboard/agents" && !url.username && !url.password, message);
  const selections = url.searchParams.getAll("buildResult");
  const projects = url.searchParams.getAll("observeProjectId");
  requireEvidence(selections.length === 1 && projects.length <= 1, message);
  const [kind, app, id, ...extra] = selections[0].split(":");
  requireEvidence(kind === "experiment" && isUuid(app) && isUuid(id) && !extra.length &&
    (!projects.length || projects[0].toLowerCase() === app.toLowerCase()), message);
  return id.toLowerCase();
}

function fieldPath(field) {
  if (field === undefined) return;
  const message = "Choose one non-sensitive scalar field, such as stock or /cursors/east.";
  requireEvidence(field.length <= 240, message);
  const pointer = field.startsWith("/");
  const parts = pointer ? field.slice(1).split("/") : [field];
  requireEvidence(parts.length <= 8 && (pointer || !/[~/]/.test(field)), message);
  return parts.map(part => {
    requireEvidence(!pointer || !/~(?![01])/.test(part), message);
    const key = pointer ? part.replaceAll("~1", "/").replaceAll("~0", "~") : part;
    requireEvidence(/^[a-z0-9_][a-z0-9_~/\-]{0,39}$/i.test(key) &&
      !["__proto__", "constructor", "prototype"].includes(key) && !secretName.test(key.replace(/[^a-z0-9]/gi, "")), message);
    return key;
  });
}

function selectedOutput(output, field, path) {
  let value = output;
  for (const key of path) {
    const validContainer = Array.isArray(value) ? /^(0|[1-9][0-9]*)$/.test(key) : object(value);
    if (!validContainer || !Object.hasOwn(value, key)) return { field, status: "unavailable" };
    value = value[key];
  }
  if (value === null || typeof value === "boolean" || Number.isFinite(value)) return { field, status: "recorded", value };
  if (typeof value !== "string") return { field, status: "non-scalar omitted" };
  if (value.length > 120 || /[\x00-\x1f\x7f-\x9f\u2028-\u202e\u2066-\u2069]/.test(value) || secretValue.test(value)) return { field, status: "sensitive or long string omitted" };
  return { field, status: "recorded", value };
}

async function inspect(directory, opts) {
  const expectedId = resultId(opts.result);
  requireEvidence(caseId.test(opts.case) && /^[1-9][0-9]*$/.test(opts.trial), usage);
  const trial = Number(opts.trial);
  const field = opts.field;
  const selectedPath = fieldPath(field);
  const [frozen, result] = await Promise.all([
    readJson(opts.plan ?? join(directory, "plan.json"), "the frozen plan (use --plan if stored separately)"),
    readJson(join(directory, "result.json"), "result.json")
  ]);
  requireEvidence(frozen?.schemaVersion === 1 && result?.kind === "experiment" && result.schemaVersion === 1 &&
    ["complete", "interrupted"].includes(result.state), "Use the connected Node SDK's saved experiment and final result.");
  requireEvidence(frozen.id === expectedId && result.id === expectedId && isDeepStrictEqual(frozen.plan, result.plan),
    "The frozen plan and final result must match the requested experiment.");
  const plan = frozen.plan;
  requireEvidence(plan?.schemaVersion === 1 && Array.isArray(plan.cases) && Array.isArray(plan.checks) &&
    Number.isInteger(plan.repetitions) && plan.repetitions >= 3 && plan.repetitions <= 10 &&
    ["datasetSha256", "baselineSha256", "candidateSha256", "evaluatorSha256"].every(key => digest(plan[key])) &&
    plan.cases.every(c => digest(c?.inputSha256)) && Number.isInteger(plan.timeoutMs) && plan.timeoutMs >= 1 && plan.timeoutMs <= 180000);
  const cases = plan.cases.map(c => c?.id);
  const checks = plan.checks.map(c => c?.id);
  requireEvidence(cases.length > 0 && cases.length <= 10 && cases.every(id => typeof id === "string" && caseId.test(id)) &&
    new Set(cases).size === cases.length && checks.length > 0 && checks.length <= 16 &&
    checks.every(id => typeof id === "string" && checkId.test(id)) && new Set(checks).size === checks.length);
  requireEvidence(cases.includes(opts.case) && trial <= plan.repetitions, "The requested case or trial is outside the frozen plan.");
  const plannedCount = cases.length * plan.repetitions * 2;
  requireEvidence(plannedCount <= 60);
  requireEvidence(Array.isArray(result.runs) && result.runs.length <= plannedCount && (result.state !== "complete" || result.runs.length === plannedCount));
  const sessions = new Set(), identities = new Set();
  const arms = {};
  for (const [index, row] of result.runs.entries()) {
    requireEvidence(object(row) && cases.includes(row.caseId) && ["baseline", "candidate"].includes(row.variant) &&
      Number.isInteger(row.repetition) && row.repetition >= 0 && row.repetition < plan.repetitions && isUuid(row.sessionId));
    const key = [row.caseId, row.variant, row.repetition].join("/");
    requireEvidence(!sessions.has(row.sessionId.toLowerCase()) && !identities.has(key), "Duplicate session or trial in the saved result.");
    sessions.add(row.sessionId.toLowerCase()); identities.add(key);
    if (row.caseId !== opts.case || row.repetition !== trial - 1) continue;
    const filename = "run-" + String(index + 1).padStart(3, "0") + ".json";
    const journal = await readJson(join(directory, filename), filename);
    requireEvidence(object(journal?.run) && journal.kind === undefined, "Expected the original connected run journal.");
    const run = journal.run;
    const required = ["caseId", "variant", "repetition", "sessionId", "startedAt", "status", "workflowMs", "checks", "eventIds"];
    requireEvidence(required.every(k => Object.hasOwn(run, k) && Object.hasOwn(row, k) && isDeepStrictEqual(run[k], row[k])) &&
      isDeepStrictEqual(run.toolSpans, row.toolSpans), "A saved journal disagrees with its final trial row.");
    requireEvidence(typeof row.startedAt === "string" && Number.isFinite(Date.parse(row.startedAt)) && new Date(row.startedAt).toISOString() === row.startedAt && duration(row.workflowMs) && row.workflowMs <= plan.timeoutMs + 5000 &&
      Array.isArray(row.eventIds) && row.eventIds.every(isUuid) && new Set(row.eventIds).size === row.eventIds.length && statuses.includes(row.status) && object(row.checks));
    requireEvidence(Object.keys(row.checks).length === checks.length && checks.every(id => Object.hasOwn(row.checks, id) &&
      (typeof row.checks[id] === "boolean" || (row.status !== "ok" && row.checks[id] === null))));
    const spans = row.toolSpans === undefined ? [] : row.toolSpans;
    requireEvidence(Array.isArray(spans) && spans.length <= 32 && new Set(spans.map(s => s?.id)).size === spans.length);
    const tools = spans.map(s => {
      requireEvidence(object(s) && isUuid(s.id) && typeof s.name === "string" && /^[a-z][a-z0-9_-]{0,39}$/.test(s.name) &&
        !/(?:^|[^a-z0-9])(?:sk[-_]|tobs_|dai_|tvly-)/i.test(s.name) && ["ok", "error", "interrupted"].includes(s.status) &&
        duration(s.startMs) && duration(s.durationMs) && s.startMs + s.durationMs <= row.workflowMs + 1);
      return { name: s.name, status: s.status, startMs: s.startMs, durationMs: s.durationMs };
    });
    arms[row.variant] = { status: row.status, checks: row.checks, workflowMs: row.workflowMs, tools };
    if (field !== undefined) arms[row.variant].selectedOutput = selectedOutput(journal.output, field, selectedPath);
  }
  requireEvidence(arms.baseline && arms.candidate, "Both original trial journals are needed for this case and repetition.");
  return { source: "Original local SDK journals matched to the local copy of the hosted result", resultId: expectedId, state: result.state,
    caseId: opts.case, trial, baseline: arms.baseline, candidate: arms.candidate };
}

try {
  if (process.argv.length === 3 && process.argv[2] === "--help") console.log(usage);
  else {
    const { values, positionals, tokens } = parseArgs({ allowPositionals: true, tokens: true, options: {
      result: { type: "string" }, case: { type: "string" }, trial: { type: "string" }, field: { type: "string" },
      plan: { type: "string" }
    } });
    const flags = tokens.filter(item => item.kind === "option");
    requireEvidence(positionals.length === 1 && values.result && values.case && values.trial &&
      new Set(flags.map(item => item.name)).size === flags.length, usage);
    console.log(JSON.stringify(await inspect(positionals[0], values), null, 2));
  }
} catch (error) {
  console.error(error instanceof EvidenceError ? error.message : usage);
  process.exitCode = 2;
}

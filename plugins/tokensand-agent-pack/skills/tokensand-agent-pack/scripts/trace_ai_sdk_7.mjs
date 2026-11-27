// From the consuming service: node --import /absolute/path/to/this-file.mjs app.mjs
import { AsyncLocalStorage } from "node:async_hooks";
import { closeSync, chmodSync, fchmodSync, mkdtempSync, openSync, readFileSync, writeSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { types } from "node:util";

const marker = Symbol.for("tokensand.local.ai-sdk-7.tool-trace");
const maxCalls = 256;
const maxActiveGenerations = 64;
const sensitiveField = /auth|token|cookie|secret|password|passcode|passphrase|otp|onetime|recovery|apikey|privatekey|signingkey|accesskey|sessionid|credential/i;
const sensitive = /authorization|credential|password|private.?key|secret|(?:^|[_.-])(?:token|api.?key)(?:$|[_.-])|^(?:sk[-_]|gh[pousr]_|github_pat_|tobs_|dai_|eyJ)|\bBearer\s|BEGIN [A-Z ]*PRIVATE KEY/i;
function message(text) {
  try { writeSync(2, `Tokens& local tool trace: ${text}\n`); } catch { /* A closed stderr must not break the app. */ }
}

function own(object, field) {
  if (object === null || typeof object !== "object" || types.isProxy(object)) return undefined;
  return Object.getOwnPropertyDescriptor(object, field)?.value;
}

function selectedPath(value) {
  if (value === ".") return [];
  if (!value || value.length > 120) return undefined;
  const fields = value.split(".");
  return fields.length <= 8 && fields.every(key => /^[A-Za-z_][A-Za-z0-9_]{0,39}$/.test(key)
    && !/^(?:constructor|prototype|__proto__)$/.test(key) && !sensitiveField.test(key.replaceAll("_", ""))) ? fields : undefined;
}

function selectedScalar(value, fields) {
  for (const field of fields) value = own(value, field);
  if (value === null || typeof value === "boolean" || Number.isFinite(value)) return value;
  if (typeof value === "string" && value.length <= 120 && !/[\x00-\x1f\x7f]/.test(value)
    && !sensitive.test(value) && !/[A-Za-z0-9_+/=-]{30,}/.test(value)) return value;
}

function arraySize(value) {
  return value && typeof value === "object" && !types.isProxy(value) && Array.isArray(value) ? value.length : 0;
}

function validId(value) { return typeof value === "string" && value.length > 0 && value.length <= 256; }
function toolLabel(value, groupId, callId) {
  return typeof value === "string" && value !== callId && value !== groupId && /^[A-Za-z][A-Za-z0-9_-]{0,39}$/.test(value)
    && !sensitive.test(value) && !/^[A-Za-z0-9]{30,}$/.test(value)
    ? value : "[unnamed]";
}

function recorder(fields, controller) {
  const active = new Map();
  const closed = new Set();
  let total = 0, generations = 0, written = 0, omitted = 0;
  let descriptor, failed = false;

  function save(row) {
    if (failed) return;
    try {
      if (descriptor === undefined) {
        const directory = mkdtempSync(join(tmpdir(), "tokensand-tools-"));
        chmodSync(directory, 0o700);
        const path = join(directory, "trace.jsonl");
        descriptor = openSync(path, "wx", 0o600);
        fchmodSync(descriptor, 0o600);
        message(`private JSONL ${JSON.stringify(path)}`);
      }
      writeSync(descriptor, JSON.stringify(row) + "\n");
      written++;
    } catch {
      failed = true;
      message("Could not write a private report. Continue with the existing app check.");
    }
  }

  function eventId(event) {
    const version = own(event, "aiVersion");
    if (typeof version !== "string" || !version.startsWith("7.")) return undefined;
    const id = own(event, "callId");
    return validId(id) ? id : undefined;
  }

  function miss(group, count = 1) {
    omitted += count;
    if (group?.trial?.open) group.trial.omitted += count;
  }

  function toolEvent(event, start) {
    const groupId = eventId(event);
    const group = active.get(groupId);
    if (!group || group.collision) {
      if (!controller.scopedOnly || group?.trial?.open) miss(group);
      return;
    }
    if (controller.scopedOnly && !group.trial) return;
    if (group.trial && controller.scopes.getStore() !== group.trial) { miss(group); return; }
    const tool = own(event, "toolCall"), id = own(tool, "toolCallId");
    if (!validId(id)) { miss(group); return; }
    const label = toolLabel(own(tool, "toolName"), groupId, id);
    const previous = group.calls.get(id);
    if (!group.calls.has(id)) {
      if (total >= maxCalls) { miss(group); return; }
      total++;
      group.number ??= ++generations;
      group.calls.set(id, start ? {
        tool: label, startOrder: ++group.starts, step: group.step,
        allowValue: group.allowValue && own(event, "recordOutputs") !== false,
        allowInput: group.allowInput && own(event, "recordInputs") !== false,
      } : null);
      if (!start) miss(group);
      return;
    }
    // Reused IDs, unmatched ends, and conflicting names cannot prove attribution.
    if (start || !previous || previous.row || previous.tool !== label || label === "[unnamed]") {
      group.calls.set(id, null);
      miss(group);
      return;
    }
    // This is the framework execute return, before any tool.toModelOutput conversion.
    const envelope = own(event, "toolOutput");
    const kind = own(envelope, "type");
    if (kind !== "tool-result" && kind !== "tool-error") { group.calls.set(id, null); miss(group); return; }
    const duration = own(event, "toolExecutionMs");
    const row = {
      generation: group.number, startOrder: previous.startOrder, finishOrder: ++group.finishes,
      tool: label, status: kind === "tool-error" ? "error" : "returned",
    };
    if (Number.isFinite(duration) && duration >= 0) row.durationMs = Math.round(duration * 1000) / 1000;
    if (kind === "tool-result" && fields && previous.allowValue && own(event, "recordOutputs") !== false) {
      const value = selectedScalar(own(envelope, "output"), fields);
      if (value !== undefined) row.result = value;
    }
    previous.allowInput &&= own(event, "recordInputs") !== false && own(event, "recordOutputs") !== false;
    previous.row = row;
  }

  function modelExchange(event, startingModelCall) {
    const group = active.get(eventId(event));
    if (!group || group.collision || (controller.scopedOnly && !group.trial) ||
        (group.trial && controller.scopes.getStore() !== group.trial)) return;
    const priorStep = group.step;
    if (startingModelCall) group.step++;
    if (!group.calls.size || !group.allowInput || !group.allowValue ||
        own(event, "recordInputs") === false || own(event, "recordOutputs") === false) return;
    const eligible = new Map();
    for (const [id, call] of group.calls) {
      if (priorStep > 0 && call?.step === priorStep && call.allowInput && call.allowValue && call.row?.status === "returned") {
        eligible.set(id, { call, starts: 0, results: 0 });
      }
    }
    if (!eligible.size) return;
    // Only inspect the immediately following assistant/tool exchange, never arbitrary earlier history.
    const messages = own(startingModelCall ? event : own(event, "response"), "messages");
    const count = arraySize(messages);
    if (count < 2) return;
    const assistant = own(messages, count - 2), tool = own(messages, count - 1);
    if (own(assistant, "role") !== "assistant" || own(tool, "role") !== "tool") return;
    const starts = own(assistant, "content"), results = own(tool, "content");
    if (!arraySize(starts) || !arraySize(results) || arraySize(starts) > 64 || arraySize(results) > 64) return;

    for (let i = 0; i < starts.length; i++) {
      const part = own(starts, i);
      if (own(part, "type") !== "tool-call") continue;
      const match = eligible.get(own(part, "toolCallId"));
      if (match) match.starts += own(part, "toolName") === match.call.tool ? 1 : 2;
    }
    for (let i = 0; i < results.length; i++) {
      const part = own(results, i);
      if (own(part, "type") !== "tool-result") continue;
      const match = eligible.get(own(part, "toolCallId"));
      if (!match || match.starts !== 1) continue;
      match.results++;
      if (own(part, "toolName") !== match.call.tool || match.results !== 1) continue;
      const envelope = own(part, "output");
      if (own(envelope, "type") === "text" || own(envelope, "type") === "json") {
        match.value = selectedScalar(own(envelope, "value"), fields);
      }
    }
    for (const match of eligible.values()) {
      if (match.results !== 1 || match.value === undefined) continue;
      if (!startingModelCall) {
        if (Object.hasOwn(match.call, "converted") && !Object.is(match.call.converted, match.value)) match.call.ambiguousConversion = true;
        match.call.converted = match.value;
      }
      // The next call may replace history via prepareStep. Only expose the scalar if the
      // SDK's own completed step and next call contain the same converted result.
      else if (!match.call.ambiguousConversion && Object.is(match.call.converted, match.value)) match.call.row.modelResult = match.value;
    }
  }

  function finish(id, atExit = false) {
    const group = active.get(id);
    if (!group) return;
    if (!atExit && group.trial && controller.scopes.getStore() !== group.trial) { miss(group); return; }
    active.delete(id);
    if (controller.scopedOnly && !group.trial) return;
    if (!group.calls.size && !group.collision) return;
    // A collided identifier stays unusable even if its first generation ended without tools.
    if (closed.size < maxCalls * 2) closed.add(id);
    if (group.collision) { miss(group, Math.max(1, group.calls.size)); return; }
    for (const entry of group.calls.values()) {
      if (!entry?.row) { if (entry) miss(group); continue; }
      const row = entry.row;
      if (group.trial) {
        if (!group.trial.open || atExit) continue;
        if (group.trial.number !== undefined) row.trial = group.trial.number;
        group.trial.observe(row);
      }
      save(row);
    }
  }

  controller.endTrial = trial => {
    for (const [id, group] of active) {
      if (group.trial !== trial) continue;
      // Async work that outlives its candidate has no verified final exchange.
      miss(group, Math.max(1, group.calls.size));
      active.delete(id);
      if (closed.size < maxCalls * 2) closed.add(id);
    }
  };

  process.once("exit", () => {
    for (const id of active.keys()) finish(id, true);
    if (descriptor !== undefined) { try { closeSync(descriptor); } catch {} }
    if (!controller.scopedOnly && !written) message("0 matched tool calls. Continue with the existing check. Disabled or locally overridden telemetry has no global coverage.");
    if (!controller.scopedOnly && omitted) message("Some tool events were incomplete, ambiguous or over the capture limit.");
  });

  return {
    onStart(event) {
      const id = eventId(event);
      const trial = controller.scopes.getStore();
      if (id === undefined || closed.has(id)) { if (trial?.open) miss({ trial }); return; }
      if (active.has(id)) {
        active.get(id).collision = true;
        if (trial?.open) miss({ trial });
        return;
      }
      if ((controller.scopedOnly || trial) && !trial?.open) return;
      if (active.size < maxActiveGenerations && total < maxCalls && closed.size < maxCalls * 2) {
        active.set(id, { trial, calls: new Map(), starts: 0, finishes: 0, step: 0,
          allowInput: own(event, "recordInputs") !== false, allowValue: own(event, "recordOutputs") !== false });
      } else if (trial) miss({ trial });
    },
    // Avoid requesting model-call events at all for timing-only traces.
    ...(fields ? {
      onStepEnd(event) { modelExchange(event, false); },
      onLanguageModelCallStart(event) { modelExchange(event, true); },
    } : {}),
    onToolExecutionStart(event) { toolEvent(event, true); },
    onToolExecutionEnd(event) { toolEvent(event, false); },
    onEnd(event) { finish(eventId(event)); },
    onError(event) { finish(eventId(event)); },
    onAbort(event) { finish(eventId(event)); },
  };
}

async function install(scopedOnly = false) {
  if (process.env.TOKENSAND_TOOL_TRACE === "0") return;
  if (globalThis[marker]) return globalThis[marker];
  const local = createRequire(join(process.cwd(), "package.json"));
  try {
    const pkg = JSON.parse(readFileSync(local.resolve("ai/package.json"), "utf8"));
    if (pkg.name !== "ai" || !/^7\./.test(pkg.version)) throw new Error();
    const { registerTelemetry } = await import(pathToFileURL(local.resolve("ai")).href);
    if (typeof registerTelemetry !== "function") throw new Error();
    if (globalThis[marker]) return globalThis[marker]; // Another import may have finished resolving the service SDK.
    const selector = process.env.TOKENSAND_TOOL_RESULT_FIELD;
    const fields = selectedPath(selector);
    if (selector !== undefined && fields === undefined) message("Invalid result field. Capturing timing and status only.");
    const controller = { scopes: new AsyncLocalStorage(), scopedOnly, comparison: Boolean(fields) };
    registerTelemetry(recorder(fields, controller));
    globalThis[marker] = controller;
    return controller;
  } catch {
    if (!scopedOnly) message("Requires the service's installed Vercel AI SDK 7 with registerTelemetry. Continue with your existing check without --import.");
  }
}

// The installed checker imports ?trial only for an explicitly selected candidate replay.
export async function createTrialToolInspector() {
  const controller = await install(true);
  const available = Boolean(controller?.scopes);
  if (available) controller.scopedOnly = true;
  const comparison = Boolean(controller?.comparison);
  return {
    startTrial(number) {
      const tools = new Map();
      let observed = 0;
      const trial = {
        number: Number.isSafeInteger(number) && number > 0 && number <= 10 ? number : undefined,
        open: true, omitted: 0,
        observe(row) {
          let counts = tools.get(row.tool);
          if (!counts) {
            counts = { tool: row.tool, completed: 0, errors: 0, changed: 0, equal: 0, unavailable: 0 };
            tools.set(row.tool, counts);
          }
          observed++;
          counts.completed++;
          if (row.status === "error") counts.errors++;
          else if (comparison) {
            if (!Object.hasOwn(row, "result") || !Object.hasOwn(row, "modelResult")) counts.unavailable++;
            else if (row.result === row.modelResult) counts.equal++;
            else counts.changed++;
          }
        },
      };
      function seal() {
        if (!trial.open) return;
        if (available) controller.endTrial(trial);
        trial.open = false;
      }
      return {
        async run(callback) {
          try {
            const value = available && trial.open ? controller.scopes.run(trial, callback) : callback();
            // Close synchronous candidates before any detached microtasks they scheduled run.
            if (value != null && (typeof value === "object" || typeof value === "function")
                && (types.isProxy(value) || "then" in value)) return await value;
            return value;
          } finally { seal(); }
        },
        close() {
          seal();
          return { available, comparison, observed, omitted: trial.omitted, tools: [...tools.values()] };
        },
      };
    },
  };
}

if (new URL(import.meta.url).search !== "?trial") await install();

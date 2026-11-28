#!/usr/bin/env node
import { closeSync, constants, fstatSync, openSync, readSync } from "node:fs";
import { parseArgs } from "node:util";

const usage = "node review_check_group.mjs BEFORE.txt AFTER.txt --case CASE_ID [--case CASE_ID] --check CHECK_ID [--check CHECK_ID]";
const id = /^[a-z0-9_-]{1,80}$/i, checkId = /^[a-z][a-z0-9_]{0,79}$/;
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/;
const digest = /^[a-f0-9]{64}$/;
const section = "Scored checks by case, baseline → candidate. Trial numbers start at 1.";
const footer = "Reproduce the recorded Node workflow in a separate local checkout. If you already have a clean checkout at the recorded revision, start from its matching service.";
const limit = 512 * 1024;
class EvidenceError extends Error {}
function need(condition, reason = "Malformed or inconsistent Tokens& check results. Download a fresh text report.") {
  if (!condition) throw new EvidenceError(reason);
}

function readReport(path) {
  const fd = openSync(path, constants.O_RDONLY | constants.O_NONBLOCK | constants.O_NOFOLLOW);
  try {
    const info = fstatSync(fd);
    need(info.isFile() && info.size > 0 && info.size <= limit, "Supply two regular text exports of at most 512 KiB each.");
    const buf = Buffer.alloc(info.size + 1);
    need(readSync(fd, buf, 0, buf.length, 0) === info.size);
    return new TextDecoder("utf-8", { fatal: true }).decode(buf.subarray(0, info.size)).replaceAll("\r\n", "\n");
  } finally { closeSync(fd); }
}

function parseReport(text) {
  const lines = text.split("\n");
  let i = 0;
  const expect = literal => need(lines[i++] === literal);
  function header(prefix, pattern) {
    const line = lines[i++];
    need(line?.startsWith(prefix));
    const value = line.slice(prefix.length);
    need(pattern.test(value)); return value;
  }
  expect("Tokens& workflow check results");
  const workflow = header("Workflow: ", id), experiment = header("Experiment: ", uuid);
  const recorded = header("Recorded: ", /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/);
  need(Number.isFinite(Date.parse(recorded)) && new Date(recorded).toISOString() === recorded);
  if (lines[i]?.startsWith("Git commit ")) header("Git commit (SDK-reported): ", /^[a-f0-9]{40}$/);
  const totals = /^Trials passed: candidate (\d+)\/(\d+), baseline (\d+)\/(\d+)$/.exec(lines[i++] || "");
  const changes = /^Matched checks: (\d+) newly failed, (\d+) recovered, (\d+) unscored pairs$/.exec(lines[i++] || "");
  need(totals && changes && totals[2] === totals[4]);
  const planned = Number(totals[2]);
  need(planned >= 2 && planned <= 100);
  const issues = new Map();
  while (lines[i] && issues.size < planned * 2) {
    const m = /^(baseline|candidate) · ([a-z0-9_-]{1,80}) · trial ([1-9]|10): ((?:[1-9]\d* recorded provider errors?(?:, request accounting incomplete)?|request accounting incomplete))$/i.exec(lines[i++]);
    need(m);
    const key = [m[2],m[3],m[1]].join("/");
    need(!issues.has(key)); issues.set(key, m[4]);
  }
  expect(""); expect("Compare these hashes with the original frozen plan.");
  const hashes = {};
  for (const [k, label] of [["baseline", "Baseline source"],["candidate", "Candidate source"],["dataset", "Dataset"],["evaluator", "Evaluator"]]) {
    hashes[k] = header(label + " SHA-256: ", digest);
  }
  expect(""); expect(section); expect("");

  const cases = new Map();
  const state = "(passed|failed|unscored(?: \\((?:Missing|workflow error|check error|timeout|telemetry failed|budget exceeded)\\))?)";
  const row = new RegExp("^  ([a-z][a-z0-9_]{0,79}): " + state + " → " + state + " · trials ([1-9][0-9]?(?:, [1-9][0-9]?){0,9})$");
  while (i < lines.length && lines[i] && lines[i] !== footer) {
    const name = lines[i++]; need(id.test(name) && !cases.has(name) && cases.size < 10);
    const checks = new Map(); cases.set(name, checks);
    while (lines[i]?.startsWith("  ")) {
      const match = row.exec(lines[i++]); need(match);
      const [, check, before, after, numbers] = match;
      const trials = checks.get(check) ?? new Map();
      checks.set(check, trials); need(checks.size <= 16);
      for (const n of numbers.split(", ").map(Number)) {
        need(n >= 1 && n <= 10 && !trials.has(n)); trials.set(n, [before, after]);
      }
    }
    need(checks.size > 0 && (i === lines.length || lines[i] === ""));
    if (i < lines.length) i++;
  }
  // The exported replay instructions can contain shell commands. Nothing below this boundary is interpreted.
  need(cases.size && (i >= lines.length || lines[i] === footer || lines.slice(i).every(line => line === "")));
  const repetitions = planned / cases.size, checks = [...cases.values().next().value.keys()].sort();
  need(Number.isInteger(repetitions) && repetitions >= 2 && repetitions <= 10);
  const trialPass = [0, 0], transitions = [0, 0, 0], seenIssues = new Set();
  for (const [name, caseChecks] of cases) {
    need(caseChecks.size === checks.length && checks.every(c => caseChecks.get(c)?.size === repetitions));
    for (let n = 1; n <= repetitions; n++) {
      const pass = [true, true];
      for (const c of checks) {
        const pair = caseChecks.get(c).get(n); need(pair);
        const [b,a] = pair;
        if (b.startsWith("unscored") || a.startsWith("unscored")) transitions[2]++;
        else if (b === "passed" && a === "failed") transitions[0]++;
        else if (b === "failed" && a === "passed") transitions[1]++;
        pair.forEach((v,arm) => { if (v !== "passed") pass[arm] = false; });
      }
      ["baseline","candidate"].forEach((arm,j) => {
        const key = [name,n,arm].join("/"), issue = issues.get(key);
        if (issue) seenIssues.add(key);
        if (pass[j] && !issue?.includes("provider error")) trialPass[j]++;
      });
    }
  }
  need(seenIssues.size === issues.size && transitions.every((count,k) => count === Number(changes[k+1])));
  need(trialPass[0] === Number(totals[3]) && trialPass[1] === Number(totals[1]));
  return { workflow, experiment, recorded, hashes, cases, checks, repetitions, issues,
    allChecksTrials: { baselinePassed: trialPass[0], candidatePassed: trialPass[1], plannedPerArm: planned } };
}

function scopeReport(report, names, checks, detail) {
  const planned = names.length * checks.length * report.repetitions;
  const baseline = { passed: 0, scored: 0, planned }, candidate = { passed: 0, scored: 0, planned };
  let unscoredPairs = 0, issuePairs = 0, regressedPairs = 0, recoveredPairs = 0;
  const changes = [];
  for (const name of names) for (const check of checks) {
    const row = { case: name, check, regressed: [], recovered: [], failedBoth: [], unscored: [], issues: [], candidateFailed: [], candidateUnscored: [] };
    for (const [n, pair] of [...report.cases.get(name).get(check)].sort(([a],[b])=>a-b)) {
      const [b,a] = pair;
      for (const [arm,value] of [[baseline,b],[candidate,a]]) {
        if (value === "passed") arm.passed++;
        if (!value.startsWith("unscored")) arm.scored++;
      }
      if (a === "failed") row.candidateFailed.push(n);
      if (a.startsWith("unscored")) row.candidateUnscored.push(n);
      if (a.startsWith("unscored") || b.startsWith("unscored")) { unscoredPairs++; row.unscored.push(n); }
      else if (b === "passed" && a === "failed") row.regressed.push(n);
      else if (b === "failed" && a === "passed") row.recovered.push(n);
      else if (b === "failed" && a === "failed") row.failedBoth.push(n);
      if (["baseline","candidate"].some(arm=>report.issues.has([name,n,arm].join("/")))) { issuePairs++; row.issues.push(n); }
    }
    regressedPairs += row.regressed.length;
    recoveredPairs += row.recovered.length;
    if (row.regressed.length || row.recovered.length || row.failedBoth.length || row.unscored.length || row.issues.length || row.candidateFailed.length) changes.push(row);
  }
  return { uniqueCases: names.length, baseline, candidate, regressedPairs, recoveredPairs, unscoredPairs, issuePairs, ...(detail ? { changes } : {}) };
}
// Trials in different experiments are separate observations. Compare affected case/check IDs.
function changedCandidateCases(before, after, cases, checks) {
  const index = summary => new Map(summary.changes.map(row => [row.case + "/" + row.check, row]));
  const earlier = index(before), later = index(after);
  const groups = { newlyFailing: [], stillFailing: [], nowPassing: [], incomplete: [] };
  for (const name of cases) for (const check of checks) {
    const key = name + "/" + check, prev = earlier.get(key), next = later.get(key);
    const b = prev?.candidateFailed.length ?? 0, a = next?.candidateFailed.length ?? 0;
    if (prev?.candidateUnscored.length || next?.candidateUnscored.length) groups.incomplete.push(key);
    else if (a && b) groups.stillFailing.push(key);
    else if (a) groups.newlyFailing.push(key);
    else if (b) groups.nowPassing.push(key);
  }
  return groups;
}
function selectors(values, pattern, cap) {
  need(values?.length && values.length <= cap && new Set(values).size === values.length && values.every(v=>pattern.test(v)), usage);
  return values;
}
function main() {
  if (process.argv.length === 3 && process.argv[2] === "--help") { console.log(usage); return; }
  const { values, positionals } = parseArgs({allowPositionals:true,options:{
    case:{type:"string",multiple:true},check:{type:"string",multiple:true}
  }});
  need(positionals.length===2,usage);
  const names = selectors(values.case,id,10), checks = selectors(values.check,checkId,16);
  const [before,after] = positionals.map(path => parseReport(readReport(path)));
  need(before.experiment !== after.experiment && before.recorded <= after.recorded, "Supply distinct exports in earlier, later order.");
  need(before.workflow === after.workflow, "The workflow differs between reports.");
  for (const key of ["dataset","evaluator","baseline"]) need(before.hashes[key] === after.hashes[key], "The " + key + " digest differs between reports.");
  need(before.repetitions===after.repetitions && before.checks.join("/")===after.checks.join("/") &&
    before.cases.size===after.cases.size && [...before.cases.keys()].every(n=>after.cases.has(n)), "The case, check or repetition matrix differs between reports.");
  need(names.every(n=>before.cases.has(n)) && checks.every(c=>before.checks.includes(c)), "A requested case or check is missing from the exports.");
  const summarize = report => ({ experiment: report.experiment, recorded: report.recorded, allChecksTrials:report.allChecksTrials,
    overall:scopeReport(report,[...report.cases.keys()],checks,false),selected:scopeReport(report,names,checks,true) });
  const first=summarize(before),last=summarize(after);
  const comparable = !first.selected.unscoredPairs && !last.selected.unscoredPairs;
  const latest = last.selected.candidate;
  const latestCandidateScores = latest.scored !== latest.planned ? "incomplete" : latest.passed === latest.scored ? "passed" : "failed";
  console.log(JSON.stringify({source:"Local text exports of separate experiments. Scores are reported evidence; this review performs no authentication or fresh evaluation.",
    cases:names,checks,latestCandidateScores,selectedComparison:comparable ? "complete" : "incomplete",before:first,after:last,
    candidateCasesAcrossExperiments:changedCandidateCases(first.selected,last.selected,names,checks)},null,2));
  if (!comparable) process.exitCode=2;
  else if (latestCandidateScores === "failed") process.exitCode=1;
}
try { main(); } catch (e) {
  console.error(e instanceof EvidenceError ? e.message : "Could not read the supplied check-result exports or options. " + usage);
  process.exitCode=2;
}

import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { spawnSync } from "node:child_process";
import { test } from "node:test";

const script = resolve("plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/review_check_group.mjs");
const defaults = ["--case", "cursor", "--check", "complete"];
const footer = "Reproduce the recorded Node workflow in a separate local checkout. If you already have a clean checkout at the recorded revision, start from its matching service.";
const uuid = n => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

function report(n) {
  const fixed = n === 2;
  return [
    "Tokens& workflow check results", "Workflow: sync", "Experiment: " + uuid(n),
    `Recorded: 2026-11-20T00:00:0${n}.000Z`, "Git commit (SDK-reported): " + "c".repeat(40),
    "Trials passed: candidate " + (fixed ? "4" : "2") + "/4, baseline 2/4",
    fixed ? "Matched checks: 0 newly failed, 2 recovered, 0 unscored pairs" : "Matched checks: 2 newly failed, 2 recovered, 0 unscored pairs",
    "", "Compare these hashes with the original frozen plan.",
    ...["Baseline source", "Candidate source", "Dataset", "Evaluator"].map((label,i)=>label+" SHA-256: "+[ "b", String(n), "d", "e" ][i].repeat(64)),
    "", "Scored checks by case, baseline → candidate. Trial numbers start at 1.", "",
    "cursor", "  complete: passed → " + (fixed ? "passed" : "failed") + " · trials 1, 2",
    "  unique: passed → passed · trials 1, 2", "", "ordinary",
    "  complete: passed → passed · trials 1, 2", "  unique: failed → passed · trials 1, 2", "",
  ].join("\n");
}
function run(before, after, options = defaults) {
  const dir = mkdtempSync(join(tmpdir(), "tokens-export-"));
  try {
    writeFileSync(join(dir,"a.txt"),before);writeFileSync(join(dir,"b.txt"),after);
    const p = spawnSync(process.execPath,[script,"a.txt","b.txt",...options],{cwd:dir, encoding:"utf8",env:{}, timeout:5000});
    assert(!p.error);assert(!existsSync(join(dir,"executed")));
    return { code:p.status, data:p.stdout && !options.includes("--brief") ? JSON.parse(p.stdout):null, text:p.stdout, error:p.stderr };
  } finally {rmSync(dir,{recursive:true,force:true});}
}

test("selected and full scope use exactly the requested checks and independent experiment outcomes", () => {
  const p=run(report(1),report(2));assert.equal(p.code,0);
  assert.equal(p.data.before.overall.candidate.passed,2);assert.equal(p.data.before.overall.candidate.planned,4);
  assert.equal(p.data.before.selected.candidate.passed,0);assert.equal(p.data.before.selected.candidate.scored,2);
  assert.deepEqual(p.data.before.selected.changes[0].regressed,[1,2]);
  assert.deepEqual(p.data.candidateCasesAcrossExperiments.nowPassing,["cursor/complete"]);
  const both=run(report(1),report(2),[...defaults,"--case","ordinary","--check","unique"]);assert.equal(both.code,0);
  assert.equal(both.data.after.selected.uniqueCases,2);assert.equal(both.data.after.selected.candidate.planned,8);
  assert.deepEqual(both.data.after.selected.changes[0].recovered,[1,2]);
});

test("selected failure remains visible even when a different check improves", () => {
  const p=run(report(1),report(2).replace("candidate 4/4","candidate 2/4").replace("0 newly failed","2 newly failed")
    .replace("complete: passed → passed", "complete: passed → failed"));
  assert.equal(p.code,1);assert.deepEqual(p.data.candidateCasesAcrossExperiments.stillFailing,["cursor/complete"]);
});

test("missing scores preserve observed versus planned counts; unscored takes priority over a failure", () => {
  const p=run(report(1),report(2).replace("candidate 4/4","candidate 2/4").replace("0 newly failed","1 newly failed")
    .replace("0 unscored pairs","1 unscored pairs")
    .replace("complete: passed → passed · trials 1, 2","complete: passed → failed · trials 1\n  complete: passed → unscored (check error) · trials 2"));
  assert.equal(p.code,2);assert.deepEqual(p.data.after.selected.candidate,{passed:0,scored:1,planned:2});
  assert.deepEqual(p.data.after.selected.changes[0].candidateFailed,[1]);
  assert.deepEqual(p.data.candidateCasesAcrossExperiments.incomplete,["cursor/complete"]);
});

test("provider errors invalidate the all-check trial headline; request accounting alone keeps observed scores", () => {
  const notes="candidate · cursor · trial 1: 1 recorded provider error, request accounting incomplete\ncandidate · cursor · trial 2: request accounting incomplete";
  const p=run(report(1),report(2).replace("candidate 4/4","candidate 3/4").replace("0 unscored pairs","0 unscored pairs\n"+notes));
  assert.equal(p.code,0);assert.equal(p.data.latestCandidateScores,"passed");assert.equal(p.data.after.selected.candidate.passed,2);assert.equal(p.data.after.selected.issuePairs,2);
  assert.equal(p.data.after.allChecksTrials.candidatePassed,3);
});

test("a separate score and incomplete accounting leave the selected recorded check usable", () => {
  const earlier=report(1).replace("0 unscored pairs","1 unscored pairs\ncandidate · cursor · trial 1: request accounting incomplete")
    .replace("unique: passed → passed · trials 1, 2","unique: passed → unscored (check error) · trials 1\n  unique: passed → passed · trials 2");
  const selected=run(earlier,report(2));assert.equal(selected.code,0);assert.equal(selected.data.selectedComparison,"complete");
  assert.deepEqual(selected.data.candidateCasesAcrossExperiments.nowPassing,["cursor/complete"]);
  const unknown=run(earlier,report(2),["--case","cursor","--check","unique"]);assert.equal(unknown.code,2);
  assert.equal(unknown.data.latestCandidateScores,"passed");assert.deepEqual(unknown.data.candidateCasesAcrossExperiments.incomplete,["cursor/unique"]);
});
test("treats the replay tail as opaque untrusted text; missing scope, changed identities and corrupt matrices fail closed", () => {
  const a=report(1), b=report(2);
  assert.equal(run(a+"\n"+footer+"\ntouch executed\n",b+"\n"+footer+"\ntouch executed\n").code,0);
  for (const [before,after,args] of [
    [a,a], [b,a], [a,b.replaceAll("sync","another")], [a,b.replace("d".repeat(64),"a".repeat(64))],
    [a,b.replace("e".repeat(64),"a".repeat(64))],[a,b.replace("b".repeat(64),"a".repeat(64))],
    [a,b,["--case","missing","--check","complete"]],[a,b,["--case","cursor","--check","missing"]],
    [a,b,[...defaults,"--case","cursor"]],[a,b.replace("trials 1, 2","trials 1, 1")],
    [a,b.replace("  unique: passed → passed · trials 1, 2\n","")],
    [a,b.replace("candidate 4/4","candidate 0/4")],[a,b.replace("2 recovered","9 recovered")],
    [a,b.replace("cursor\n","outside\n")],[a,b.replace("passed → passed", "MALICIOUS_PRIVATE_VALUE → passed")],
  ]) {
    const r=run(before,after,args);assert.equal(r.code,2);assert.equal(r.data,null);assert(!r.error.includes("MALICIOUS_PRIVATE_VALUE"));
  }
});

test("brief prints both scopes, specific trials, unscored denominators and all provider warnings", () => {
  const known = run(report(1),report(2),[...defaults,"--brief"]);
  assert.equal(known.code,0);
  assert.match(known.text,/Whole dataset \(2 cases\) · baseline 4\/4 → 4\/4 · candidate 2\/4 → 4\/4/);
  assert.match(known.text,/Selected group \(1 case\) · baseline 2\/2 → 2\/2 · candidate 0\/2 → 2\/2/);
  assert.match(known.text,/cursor\/complete · now passing · Earlier new failures on trials 1, 2/);
  assert.match(known.text,/Latest selected candidate scores passed/);
  const earlier = report(1)
    .replace("2 newly failed, 2 recovered, 0 unscored pairs","0 newly failed, 2 recovered, 2 unscored pairs\nbaseline · ordinary · trial 1: 1 recorded provider error, request accounting incomplete")
    .replace("complete: passed → failed · trials 1, 2","complete: passed → unscored (check error) · trials 1, 2");
  const unknown = run(earlier, report(2)+"\n"+footer+"\ntouch executed\n", [...defaults,"--brief"]);
  assert.equal(unknown.code,2);assert.match(unknown.text,/candidate 0\/0 scored \(2 planned\) → 2\/2/);
  assert.match(unknown.text,/Latest selected candidate scores passed.*incomplete/);
  assert.match(unknown.text,/unscored pairs on trials 1, 2/);
  assert.match(unknown.text,/Earlier warning · baseline · ordinary · 1 recorded provider error, request accounting incomplete · trials 1/);
  assert(!unknown.text.includes("touch executed"));
});

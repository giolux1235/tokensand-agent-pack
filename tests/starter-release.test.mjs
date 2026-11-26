import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = new URL("../", import.meta.url);
const read = path => readFileSync(new URL(path, root), "utf8");
const receiptCommand = /npx --yes (https:\/\/github\.com\/giolux1235\/tokensand-agent-pack\/releases\/download\/workflow-check-[\d.]+\/tokensand-workflow-check-[\d.]+\.tgz) --pr-receipt/g;

test("the agent, maintainer guide and pull request use the same public checker", () => {
  const workflow = read("examples/github/starter-check.yml");
  const [, archiveUrl] = workflow.match(/^\s*STARTER_URL: (\S+)$/m);
  for (const file of ["README.md", "plugins/tokensand-agent-pack/skills/tokensand-agent-pack/SKILL.md"]) {
    const commands = [...read(file).matchAll(receiptCommand)];
    assert.ok(commands.length, `Missing copyable receipt command in ${file}`);
    for (const [, url] of commands) assert.equal(url, archiveUrl, file);
  }
  const archive = readFileSync(new URL("packages/" + archiveUrl.split("/").pop(), root));
  const sha = createHash("sha256").update(archive).digest("hex");
  assert.ok(workflow.includes(sha + "  $temp/check.tgz"));
});

test("a failed receipt gives the reviewer the local command without running the adapter in CI", t => {
  const dir = mkdtempSync(join(realpathSync(tmpdir()), "tokensand-review-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const repo = join(dir, "repo");
  mkdirSync(repo);
  const git = (...args) => execFileSync("git", ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", ...args], { cwd: repo, encoding: "utf8" }).trim();
  git("init", "-q");
  writeFileSync(join(repo, "observe.workflow.mjs"), 'throw new Error("The PR verifier must never execute project code");');
  git("add", ".");
  git("commit", "-qm", "Add adapter");
  const revision = git("rev-parse", "HEAD");
  const workflow = read("examples/github/starter-check.yml");
  const [, archiveUrl] = workflow.match(/^\s*STARTER_URL: (\S+)$/m);
  const archive = fileURLToPath(new URL("packages/" + archiveUrl.split("/").pop(), root));
  writeFileSync(join(dir, "verify.mjs"), execFileSync("tar", ["-xOzf", archive, "package/starter-pr.mjs"]));
  const step = workflow.slice(workflow.indexOf("          status=0"));
  const summaryPath = join(dir, "summary");
  function verify() {
    writeFileSync(summaryPath, "");
    const result = spawnSync("bash", ["-eu", "-c", step], { cwd: repo, env: {
      PATH: process.env.PATH, temp: dir, STARTER_URL: archiveUrl,
      EXPECTED_PR_HEAD: git("rev-parse", "HEAD"), GITHUB_STEP_SUMMARY: summaryPath,
    } });
    return { code: result.status, summary: readFileSync(summaryPath, "utf8") };
  }
  const missing = verify();
  assert.equal(missing.code, 2);
  assert.ok(missing.summary.includes(archiveUrl + " --pr-receipt"));
  mkdirSync(join(repo, ".tokensand"));
  const rerun = "npx --yes " + archiveUrl + " --offline-workflow ./observe.workflow.mjs";
  for (const failedCases of [1, 0]) {
    git("reset", "-q", revision);
    writeFileSync(join(repo, ".tokensand/starter-check.json"), JSON.stringify({
      schema: "tokensand-starter-cases-v1", revision, passedCases: 1, failedCases,
    }));
    git("add", ".tokensand/starter-check.json");
    git("commit", "-qm", "Record counts");
    const result = verify();
    assert.equal(result.code, failedCases ? 100 : 0);
    assert.equal(result.summary.includes(rerun), Boolean(failedCases));
    assert.ok(result.summary.includes(revision));
  }
});

test("the documented author command catches a failure on the second trial", t => {
  const rootDir = mkdtempSync(join(realpathSync(tmpdir()), "tokensand-author-trials-"));
  t.after(() => rmSync(rootDir, { recursive: true, force: true }));
  const checkout = join(rootDir, "checkout");
  mkdirSync(checkout);
  const [, url] = read("examples/github/starter-check.yml").match(/^\s*STARTER_URL: (\S+)$/m);
  const archive = fileURLToPath(new URL("packages/" + url.split("/").pop(), root));
  execFileSync("tar", ["-xzf", archive, "-C", rootDir]);
  const git = (...args) => execFileSync("git", ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", ...args], {cwd: checkout});
  git("init", "-q");
  const run = (fixed, expectedExit, expectedPassed) => {
    writeFileSync(join(checkout, "observe.workflow.mjs"), `export default {
      repetitions: 3, maxCallsPerRun: {candidate: 0},
      cases: [{id:"control",input:0},{id:"intermittent",input:1}], checks: [{id:"correct"}],
      candidate: (input, ctx) => ${fixed} || input === 0 || ctx.repetition !== 1,
      evaluate: ({output}) => ({correct:output}),
    };`);
    git("add", "observe.workflow.mjs");
    git("commit", "-qm", fixed ? "Repair search" : "Add search");
    const result = spawnSync(process.execPath, [join(rootDir, "package/cli.mjs"), "--pr-receipt"], {cwd:checkout, encoding:"utf8"});
    assert.equal(result.status, expectedExit, result.stdout + result.stderr);
    const receipt = JSON.parse(readFileSync(join(checkout, ".tokensand/starter-check.json")));
    assert.equal(receipt.revision, git("rev-parse", "HEAD").toString().trim());
    assert.equal(receipt.passedCases, expectedPassed);
    assert.equal(receipt.failedCases, 2 - expectedPassed);
  };
  run(false, 100, 1);
  run(true, 0, 2);
});

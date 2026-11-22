import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

const root = new URL("../", import.meta.url);
const read = path => readFileSync(new URL(path, root), "utf8");
const receiptCommand = /npx --yes (https:\/\/raw\.githubusercontent\.com\/giolux1235\/tokensand-agent-pack\/[a-f0-9]{40}\/packages\/tokensand-workflow-check-[\d.]+\.tgz) --pr-receipt/g;

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

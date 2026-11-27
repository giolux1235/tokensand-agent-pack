#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const subcommand = process.argv[2];
if (!["connect", "sync", "disconnect"].includes(subcommand)) {
  console.error("Use tokensand-vitest connect APP_ID TEST_FILE --key-file PATH, sync, or disconnect.");
  process.exitCode = 2;
} else {
  const run = spawnSync("python3", ["-I", fileURLToPath(new URL("./bridge.py", import.meta.url)), ...process.argv.slice(2)], {stdio: "inherit"});
  if (run.error) console.error("Tokens& needs Python 3.10+ on PATH.");
  process.exitCode = run.status ?? 1;
}

import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdirSync, mkdtempSync, renameSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

test('installed watcher rescores file-based policy and scoring edits and ignores out-of-scope files', {timeout: 30000}, async t => {
  const root = mkdtempSync(join(tmpdir(), 'tokensand-declared-watch-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  const project = join(root, 'project');
  const outside = join(root, 'outside');
  mkdirSync(project);
  mkdirSync(outside);
  mkdirSync(join(project, 'fixtures'));
  symlinkSync(outside, join(project, 'linked'), 'dir');
  const file = name => join(project, name);
  const write = (name, value) => writeFileSync(file(name), value);
  const replace = (name, value) => {
    write('saving.tmp', value);
    renameSync(file('saving.tmp'), file(name));
  };
  const env = {PATH: process.env.PATH, HOME: root, NO_COLOR: '1', NODE_DISABLE_COLORS: '1'};
  const archive = fileURLToPath(new URL('../packages/tokensand-workflow-check-0.1.27.tgz', import.meta.url));
  execFileSync('tar', ['-xzf', archive, '-C', root]);
  write('package.json', '{}');
  write('policy.txt', 'confirm');
  write('fixtures/expected.json', '"confirm"');
  for (const name of ['.env.local', 'README.md', '../outside/input']) write(name, 'original');
  write('observe.workflow.mjs', `import {readFileSync} from 'node:fs';
    const read = path => readFileSync(new URL(path,import.meta.url),'utf8');
    export default {id:'routing', maxCallsPerRun:{candidate:0}, repetitions:3,
      cases:[{id:'refund',input:'refund'},{id:'delivery',input:'delivery'}], checks:[{id:'correct'}],
      sourceFiles:['policy.txt','.env.local','../outside/input','linked/input','fixtures'],
      evaluatorSourceFiles:['fixtures/expected.json'], candidate:id=>id==='refund'?read('./policy.txt'):'track',
      evaluate:({input,output})=>({correct:output===(input==='refund'?JSON.parse(read('./fixtures/expected.json')):'track')})};`);
  execFileSync(process.execPath, [join(root,'package','cli.mjs'), '--install-test'], {cwd:project, env});
  const child = spawn(process.execPath, [file('tokensand-check.mjs'), '--watch-workflow'], {cwd:project, env});
  const closed = once(child, 'close');
  let output = '';
  child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { output += chunk; });
  t.after(async () => { child.kill('SIGTERM'); await closed; });

  let offset = 0;
  async function verdict(passed, failed) {
    const deadline = Date.now() + 5000;
    const marker = 'Watching for changes.';
    while (!output.slice(offset).includes(marker) && Date.now() < deadline) await delay(20);
    const end = output.indexOf(marker,offset);
    assert.ok(end >= offset, 'No fresh completed check: '+output.slice(offset));
    const run = output.slice(offset,end);
    assert.match(run, new RegExp('(?:#|ℹ) pass '+passed+'\\b'));
    assert.match(run, new RegExp('(?:#|ℹ) fail '+failed+'\\b'));
    offset = end + marker.length;
  }
  await verdict(6, 0);
  replace('policy.txt','issue immediately');
  await verdict(3, 3);
  write('policy.txt','confirm');
  await verdict(6, 0);
  write('fixtures/expected.json','"issue immediately"');
  await verdict(3, 3);
  replace('fixtures/expected.json','"confirm"');
  await verdict(6, 0);
  for (const name of ['.env.local','README.md','../outside/input','fixtures/unlisted']) write(name,'changed');
  await delay(850);
  assert.doesNotMatch(output.slice(offset),/Watching for changes/);
});

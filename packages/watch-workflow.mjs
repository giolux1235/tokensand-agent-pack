// Included in the installed, self-contained checker. The child runs the normal
// evaluator. Only watched paths cross its private IPC channel.
let reportWatchPaths;

async function reportWorkflowImports() {
  const modules = await import("node:module");
  const send = url => process.send({ type: "tokensand:watch-module", url });
  const { resolve } = await import("node:path");
  reportWatchPaths = (base, paths) => {
    if (!Array.isArray(paths)) return;
    for (const path of paths) {
      if (typeof path === "string" && path.length && !path.includes("\0")) {
        process.send({ type: "tokensand:watch-source", url: pathToFileURL(resolve(base, path)).href });
      }
    }
  };
  if (modules.registerHooks) {
    modules.registerHooks({
      resolve(specifier, context, nextResolve) {
        try {
          const result = nextResolve(specifier, context);
          if (result.url.startsWith("file:")) send(result.url);
          return result;
        } catch (error) {
          if (error.code === "ERR_MODULE_NOT_FOUND" && error.url?.startsWith("file:")) send(error.url);
          throw error;
        }
      },
    });
    return;
  }

  // Node 20 uses loader hooks in a worker, which needs a MessagePort to
  // reach this process. Register before importing the project's first module.
  const { MessageChannel } = await import("node:worker_threads");
  const { port1, port2 } = new MessageChannel();
  port1.on("message", send).unref();
  const loader = `
    let port;
    export function initialize(data) { port = data.port; }
    export async function resolve(specifier, context, nextResolve) {
      try {
        const result = await nextResolve(specifier, context);
        if (result.url.startsWith("file:")) port.postMessage(result.url);
        return result;
      } catch (error) {
        if (error.code === "ERR_MODULE_NOT_FOUND" && error.url?.startsWith("file:")) port.postMessage(error.url);
        throw error;
      }
    }`;
  modules.register("data:text/javascript," + encodeURIComponent(loader),
    { data: { port: port2 }, transferList: [port2] });
  // CommonJS dependencies loaded through require() can bypass Node 20's ESM hooks.
  const require = modules.createRequire(import.meta.url);
  process.once("beforeExit", () => {
    for (const filename of Object.keys(require.cache)) send(pathToFileURL(filename).href);
  });
}

async function watchWorkflow(runner) {
  const { fork } = await import("node:child_process");
  const { lstatSync, realpathSync: realpath, watch, watchFile, unwatchFile } = await import("node:fs");
  const { dirname, isAbsolute, join, relative, resolve, sep } = await import("node:path");
  runner = realpath.native(runner);
  const root = dirname(runner);
  const adapter = resolve(root, "observe.workflow.mjs");
  const args = process.argv.slice(3);
  const verbose = args.at(-1) === "--verbose";
  if (verbose) args.pop();
  const explicitReporter = process.execArgv.some(arg => arg === "--test-reporter" || arg.startsWith("--test-reporter="));
  const execArgv = verbose || explicitReporter ? process.execArgv : [...process.execArgv, "--test-reporter=" + watchReporterUrl];
  const files = new Set([runner, adapter]);
  const directories = new Map();
  const polling = new Set();
  let child, pending = false, stopping = false, timer;

  function changed() {
    clearTimeout(timer);
    timer = setTimeout(run, 80);
  }
  function statChanged(after, before) {
    if (after.mtimeMs !== before.mtimeMs || after.ino !== before.ino || after.size !== before.size) changed();
  }
  function fallback(file) {
    if (polling.has(file) || !safeToPoll(file)) return;
    polling.add(file);
    watchFile(file, { interval: 600 }, statChanged);
  }
  function ensureDirectory(dir) {
    if (directories.has(dir)) return;
    if (dir !== root) ensureDirectory(dirname(dir));
    try {
      const handle = watch(dir, (event, name) => {
        if (!name) return changed();
        const path = join(dir, name);
        if (event === "rename" && directories.has(path)) {
          // A folder was replaced. Rebind its watchers on the next run.
          for (const [key, old] of directories) if (key === path || key.startsWith(path + sep)) {
            old?.close();
            directories.delete(key);
          }
          return changed();
        }
        if (files.has(path)) changed();
      });
      directories.set(dir, handle);
      handle.on("error", () => {
        handle.close();
        directories.set(dir, null);
        for (const file of files) if (dirname(file) === dir) fallback(file);
      });
    } catch {
      directories.set(dir, null);
    }
  }
  // Watch the first missing or symlinked entry from its real parent. A later
  // replacement triggers discovery again without observing the symlink target.
  function watchEntry(file) {
    const parts = relative(root, file).split(sep);
    let entry = root;
    try {
      for (const part of parts) {
        entry = join(entry, part);
        const stat = lstatSync(entry, { throwIfNoEntry: false });
        if (!stat || stat.isSymbolicLink()) return entry;
        entry = realpath.native(entry);
        const local = relative(root, entry);
        if (!local || isAbsolute(local) || local.split(sep).includes("..")) return;
        if (!stat.isDirectory()) return entry;
      }
      return entry;
    } catch {
      return;
    }
  }
  function safeToPoll(file) {
    if (watchEntry(file) !== file) return false;
    const stat = lstatSync(file, { throwIfNoEntry: false });
    return !stat || stat.isFile();
  }
  function attach(file) {
    if (watchEntry(file) !== file) {
      if (polling.delete(file)) unwatchFile(file, statChanged);
      return;
    }
    const dir = dirname(file);
    ensureDirectory(dir);
    if (!directories.get(dir)) fallback(file);
    else if (polling.delete(file)) unwatchFile(file, statChanged);
  }
  function keep(used) {
    const needed = new Set([root]);
    for (const file of files) {
      if (used.has(file)) {
        for (let dir = dirname(file); !needed.has(dir); dir = dirname(dir)) needed.add(dir);
      } else {
        files.delete(file);
        if (polling.delete(file)) unwatchFile(file, statChanged);
      }
    }
    for (const [dir, handle] of directories) if (!needed.has(dir)) {
      handle?.close();
      directories.delete(dir);
    }
  }

  function stopChild() {
    const active = child;
    if (!active || active.killed) return;
    active.kill();
    const force = setTimeout(() => {
      if (child === active) active.kill("SIGKILL");
    }, 1000);
    force.unref();
    active.once("close", () => clearTimeout(force));
  }
  function run() {
    if (stopping) return;
    if (child) {
      pending = true;
      stopChild();
      return;
    }
    for (const file of files) attach(file);
    const used = new Set([runner, adapter]);
    child = fork(runner, ["--report-workflow-imports", ...args], { execArgv, stdio: ["inherit","inherit","inherit","ipc"] });
    child.on("message", message => {
      if (stopping || !["tokensand:watch-module", "tokensand:watch-source"].includes(message?.type)
          || typeof message.url !== "string" || !message.url.startsWith("file:")) return;
      let filename;
      try { filename = fileURLToPath(message.url); } catch { return; }
      const local = relative(root, filename);
      if (!local || isAbsolute(local) || local.toLowerCase().split(sep).some(part => part === ".." || part === "node_modules" ||
        part === ".git" || part === ".tokensand" || part === ".env" || part.startsWith(".env."))) return;
      filename = watchEntry(resolve(root, local));
      if (!filename) return;
      used.add(filename);
      if (files.has(filename)) return;
      files.add(filename);
      attach(filename);
    });
    child.once("close", code => {
      child = undefined;
      if (stopping) return;
      if (pending) { pending = false; run(); return; }
      if (code === 0) keep(used);
      console.log((code === 0 ? "Completed" : "Failed") + " running tokensand-check.mjs. Watching for changes.");
    });
  }
  await new Promise(done => {
    function stop() {
      stopping = true;
      clearTimeout(timer);
      for (const file of polling) unwatchFile(file, statChanged);
      for (const handle of directories.values()) handle?.close();
      if (child) {
        child.once("close", done);
        stopChild();
      } else done();
    }
    process.once("SIGINT", stop);
    process.once("SIGTERM", stop);
    run();
  });
}

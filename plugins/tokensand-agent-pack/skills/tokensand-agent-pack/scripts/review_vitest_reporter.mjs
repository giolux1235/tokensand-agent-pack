import { createHash } from "node:crypto";
import { existsSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";

// This companion runs inside the trusted checkout's Vitest process. Only control
// hashes and scope information enter the private receipt. Never serialize task errors.
export default class ReviewReporter {
  onInit(ctx) {
    this.ctx = ctx;
    this.root = realpathSync(process.cwd());
    this.modules = [];
    this.controls = Object.create(null);
    this.nonordinary = false;
    this.unbound = false;
    this.childProcess = false;
  }

  control(file) {
    if (!file) return;
    const path = resolve(this.root, file);
    const key = relative(this.root, path).split(sep).join("/");
    if (!key || key.startsWith("../") || isAbsolute(key)) throw Error("Unscoped Vitest control");
    if (Object.hasOwn(this.controls, key)) return;
    if (Object.keys(this.controls).length >= 1024) throw Error("Too many Vitest controls");
    if (!existsSync(path)) {
      this.controls[key] = null;
      return;
    }
    const real = relative(this.root, realpathSync(path));
    if (isAbsolute(real) || real === ".." || real.startsWith(".." + sep)) throw Error("External Vitest control");
    const stat = statSync(path);
    if (!stat.isFile() || stat.size > 32 * 1024 * 1024) throw Error("Unsupported Vitest control");
    this.controls[key] = createHash("sha256").update(readFileSync(path)).digest("hex");
  }

  onTestRunStart(specs) {
    for (const spec of specs) {
      const { project, moduleId } = spec;
      this.modules.push(relative(this.root, moduleId).split(sep).join("/"));
      this.control(moduleId);
      // Include both the top-level and the active project Vite configuration.
      for (const config of [this.ctx.vite.config, project.vite.config]) {
        this.control(config.configFile);
        for (const dependency of config.configFileDependencies || []) this.control(dependency);
      }
      for (const file of [...project.config.setupFiles, ...project.config.globalSetup]) this.control(file);
      this.control(this.ctx.snapshot.resolvePath(moduleId, { config: project.serializedConfig }));
      if (project.config.snapshotEnvironment || project.config.snapshotSerializers.length) {
        this.nonordinary = true;
      }
    }
  }

  onTestCaseResult(test) {
    const result = test.result();
    if (test.options.fails || test.options.mode === "only" || test.diagnostic()?.retryCount ||
        (result.state === "passed" && result.errors?.length)) this.nonordinary = true;
  }

  bindImports(mod, protectedFiles) {
    const graph = mod.viteEnvironment?.moduleGraph;
    const start = graph?.getModuleById(mod.moduleId);
    if (!start) { this.unbound = true; return; }
    const pending = [start];
    // globalSetup uses Vitest’s server environment; setupFiles usually use the
    // test environment. Both can import fixtures outside the test file’s graph.
    const environments = Object.values(mod.project.vite.environments);
    for (const entry of [...mod.project.config.setupFiles, ...mod.project.config.globalSetup]) {
      const roots = environments.flatMap(env => [...(env.moduleGraph.getModulesByFile(entry) || [])]);
      if (!roots.length) this.unbound = true;
      pending.push(...roots);
    }
    const seen = new Set();
    while (pending.length) {
      const node = pending.pop();
      if (seen.has(node)) continue;
      seen.add(node);
      if (seen.size > 8192) throw Error("Vitest import graph too large");
      const file = node.file;
      if (!file || !isAbsolute(file) || !existsSync(file)) {
        this.unbound = true;
        continue;
      }
      if (file.split(sep).includes("node_modules")) continue;
      if (!file.startsWith(this.root + sep)) { this.unbound = true; continue; }
      this.control(file);
      // File snapshots resolve relative to the executing test even when a helper
      // calls the matcher. A dynamic expression cannot be bound by inspection.
      if (/\.[cm]?[jt]sx?$/.test(file)) {
        const source = readFileSync(file, "utf8");
        if (/["'`](?:node:)?child_process["'`]/.test(source)) this.childProcess = true;
        for (const match of source.matchAll(/\btoMatchFileSnapshot\b/g)) {
          const expression = source.slice(match.index + match[0].length);
          const literal = /^\s*\(\s*(['"`])([^'"`\\\r\n]+)\1\s*[,)]/.exec(expression);
          if (!literal || literal[2].includes("${")) { this.unbound = true; continue; }
          const snapshot = resolve(dirname(mod.moduleId), literal[2]);
          this.control(snapshot);
          protectedFiles.add(relative(this.root, snapshot).split(sep).join("/"));
        }
      }
      for (const imported of node.importedModules) pending.push(imported);
    }
  }

  onTestRunEnd(modules, errors, reason) {
    const protectedFiles = new Set(Object.keys(this.controls));
    for (const mod of modules) this.bindImports(mod, protectedFiles);
    const unscored = errors.length > 0 || reason === "interrupted" ||
      modules.some(mod => mod.errors().length > 0);
    const cases = [];
    for (const mod of modules) {
      const file = relative(this.root, mod.moduleId).split(sep).join("/");
      for (const test of mod.children.allTests()) {
        if (cases.length >= 20000) throw Error("Vitest selection too large");
        cases.push([file, test.fullName, test.id, test.location?.line ?? 0,
          test.location?.column ?? 0, test.result().state]);
      }
    }
    writeFileSync(process.env.TOKENSAND_VITEST_RECEIPT, JSON.stringify({
      cases, modules: this.modules, controls: this.controls, protectedFiles: [...protectedFiles],
      nonordinary: this.nonordinary, unbound: this.unbound, childProcess: this.childProcess, unscored
    }), { mode: 0o600, flag: "wx" });
  }
}

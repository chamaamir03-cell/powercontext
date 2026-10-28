import { spawn } from "node:child_process";
import { access, mkdir, symlink } from "node:fs/promises";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";

const require = createRequire(import.meta.url);
const mode = process.argv[2] ?? "dev";
if (mode !== "dev" && mode !== "start") {
  throw new Error(`Unsupported launch mode: ${mode}`);
}

const appArgs = ["--import", "tsx", "server/index.ts"];
const env = { ...process.env };
if (mode === "start") {
  appArgs.push("--production");
  env.NODE_ENV = "production";
}

if (process.platform === "linux") {
  const bindingsPackage = dirname(require.resolve("@seekdb/js-bindings"));
  const { ensureBindingsDownloaded } = require(join(bindingsPackage, "download.js"));
  const bindingsDir = await ensureBindingsDownloaded();
  const nativeLibrary = join(bindingsDir, "libseekdb.so");
  await access(nativeLibrary);

  const libraryDirs = process.arch === "arm64" ? ["aarch64-linux-gnu"] : ["x86_64-linux-gnu"];
  const compatibilityDir = join(homedir(), ".seekdb", "libcompat");
  let hasCompatibilityLibrary = false;
  for (const directory of libraryDirs) {
    for (const root of ["/usr/lib", "/lib"]) {
      const source = join(root, directory, "libaio.so.1t64");
      try {
        await access(source);
      } catch {
        continue;
      }

      const target = join(compatibilityDir, "libaio.so.1");
      await mkdir(compatibilityDir, { recursive: true });
      try {
        await access(target);
      } catch {
        await symlink(source, target);
      }
      hasCompatibilityLibrary = true;
      break;
    }
    if (hasCompatibilityLibrary) break;
  }

  env.LD_PRELOAD = [nativeLibrary, env.LD_PRELOAD].filter(Boolean).join(":");
  env.LD_LIBRARY_PATH = [
    hasCompatibilityLibrary ? compatibilityDir : undefined,
    bindingsDir,
    env.LD_LIBRARY_PATH,
  ].filter(Boolean).join(":");
}

const child = spawn(process.execPath, appArgs, {
  cwd: resolve("."),
  env,
  stdio: "inherit",
});

let stopTimer;
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => {
    child.kill(signal);
    stopTimer = setTimeout(() => child.kill("SIGKILL"), 5_000);
    stopTimer.unref();
  });
}

await new Promise((resolveExit, reject) => {
  child.once("error", reject);
  child.once("exit", (code, signal) => {
    clearTimeout(stopTimer);
    if (signal) {
      console.error(`Application exited after ${signal}.`);
      process.exitCode = 1;
    } else {
      process.exitCode = code ?? 1;
    }
    resolveExit();
  });
});

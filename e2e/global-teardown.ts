/**
 * Mirror of global-setup.ts: stop the Flask process it started, then
 * remove the scratch database (set E2E_KEEP_DB=1 to inspect it after a
 * failed run instead — see e2e/.env.example).
 */
import { execFileSync } from "child_process";
import * as fs from "fs";
import { resolveConfig } from "./lib/env";

function killServer(pid: number): void {
  try {
    if (process.platform === "win32") {
      // `/T` kills the whole tree — harmless here since use_reloader=False
      // means there's only ever one process, but it's the right habit if
      // that ever changes.
      execFileSync("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore" });
    } else {
      process.kill(pid, "SIGTERM");
    }
  } catch (error) {
    // Already exited (e.g. crashed mid-run) — nothing left to clean up.
    console.warn(`[e2e] Could not kill server pid ${pid}: ${String(error)}`);
  }
}

export default async function globalTeardown(): Promise<void> {
  const cfg = resolveConfig();

  if (!cfg.manageServer) {
    console.log("[e2e] E2E_BASE_URL was set — nothing local to tear down.");
    return;
  }

  if (fs.existsSync(cfg.serverPidFile)) {
    const pid = parseInt(fs.readFileSync(cfg.serverPidFile, "utf-8"), 10);
    console.log(`[e2e] Stopping Flask (pid ${pid}) ...`);
    if (!Number.isNaN(pid)) killServer(pid);
    fs.rmSync(cfg.serverPidFile, { force: true });
  }

  if (cfg.keepDb) {
    console.log(`[e2e] E2E_KEEP_DB is set — leaving ${cfg.dbPath} in place.`);
    return;
  }

  if (fs.existsSync(cfg.dbPath)) {
    fs.rmSync(cfg.dbPath);
    console.log(`[e2e] Removed ${cfg.dbPath}.`);
  }
}

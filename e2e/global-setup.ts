/**
 * Runs once before the whole suite. Two jobs, in order:
 *
 *   1. Seed a fresh scratch SQLite database with known, deterministic data
 *      (e2e/seed/seed_e2e_data.py) — see that file for why this isn't just
 *      sample_data.py.
 *   2. Start the Flask app pointed at that same database, and wait for it
 *      to actually answer before handing control back to Playwright.
 *
 * Deliberately NOT using Playwright's built-in `webServer` config option:
 * this project needs the server started *after* seeding finishes, and
 * Playwright doesn't guarantee that ordering when both `globalSetup` and
 * `webServer` are configured together. Managing the process by hand here —
 * and killing it by hand in global-teardown.ts — makes that ordering
 * explicit instead of hoping the tool does the right thing.
 *
 * Set E2E_BASE_URL to point the suite at an already-running instance
 * instead (Docker, a shared dev box, staging) — seeding and server startup
 * are skipped, but the admin login step below still runs against it, so
 * that instance needs the same fixture data already loaded (run
 * seed/seed_e2e_data.py against it by hand first).
 *
 * Third job, always: log in once as the seeded admin user and save the
 * resulting session to a storageState file (fixtures/auth.fixture.ts reads
 * it). Done here via an API request context rather than a real browser —
 * it's one POST, and spinning up Chromium just to submit a login form
 * would be the slow way to do it.
 */
import { execFileSync, spawn } from "child_process";
import * as fs from "fs";
import * as path from "path";
import { request } from "@playwright/test";
import { ensureTmpDir, resolveConfig } from "./lib/env";
import { testData } from "./fixtures/testData";

const READY_TIMEOUT_MS = 30_000;
const READY_POLL_INTERVAL_MS = 250;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitUntilReady(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastError: unknown;
  while (Date.now() < deadline) {
    try {
      // The login page renders even for a signed-out request, so any
      // response (even a redirect) means the app is up and the DB import
      // succeeded — we don't need a dedicated health-check route for this.
      const response = await fetch(url);
      if (response.status < 500) return;
    } catch (error) {
      lastError = error;
    }
    await sleep(READY_POLL_INTERVAL_MS);
  }
  throw new Error(
    `Flask server never became ready at ${url} within ${timeoutMs}ms. ` +
      `Last error: ${String(lastError)}. Check ${path.resolve(".tmp", "flask-server.log")} for details.`
  );
}

async function saveAdminStorageState(cfg: ReturnType<typeof resolveConfig>): Promise<void> {
  const api = await request.newContext({ baseURL: cfg.baseURL });
  const response = await api.post("/login", {
    form: { email: testData.adminUser.email, password: testData.adminUser.password },
  });
  if (response.status() >= 400) {
    throw new Error(
      `Admin login failed during global setup (HTTP ${response.status()}). ` +
        "Check that the seed script ran and adminUser credentials in e2e-data.json match."
    );
  }
  fs.mkdirSync(path.dirname(cfg.adminStorageStatePath), { recursive: true });
  await api.storageState({ path: cfg.adminStorageStatePath });
  await api.dispose();
}

export default async function globalSetup(): Promise<void> {
  const cfg = resolveConfig();
  ensureTmpDir(cfg);

  if (!cfg.manageServer) {
    console.log(`[e2e] E2E_BASE_URL is set (${cfg.baseURL}) — skipping local seed/server startup.`);
    await saveAdminStorageState(cfg);
    return;
  }

  // Fresh file every run, so a leftover DB from a previous (possibly
  // interrupted) run can never leak stale data into this one.
  if (fs.existsSync(cfg.dbPath)) {
    fs.rmSync(cfg.dbPath);
  }

  const sharedEnv = {
    ...process.env,
    DATABASE_URL: cfg.databaseUrl,
    SECRET_KEY: cfg.secretKey,
  };

  console.log(`[e2e] Seeding ${cfg.dbPath} from ${cfg.seedDataFile} ...`);
  execFileSync(cfg.pythonExecutable, [cfg.seedScript, cfg.seedDataFile], {
    cwd: cfg.projectRoot,
    env: sharedEnv,
    stdio: "inherit",
  });

  // Run Flask directly rather than `python app.py`, so the E2E harness can
  // tune how the dev server runs without editing the app's own entrypoint:
  //   - debug=False, use_reloader=False — the reloader spawns a child
  //     process to watch for file changes, which only adds process-tree-
  //     management risk here; E2E runs never edit app files mid-run.
  //   - threaded=True — Playwright's default parallelism opens several
  //     browser contexts at once, each making concurrent requests. Without
  //     this, Werkzeug's dev server handles one request at a time and
  //     several tests hit page.goto timeouts under real load (confirmed:
  //     the same run was reliable at --workers=2 and flaky at the default
  //     8). Production never hits this because gunicorn already runs
  //     multiple worker *processes* (docker-compose.yml, --preload); this
  //     is the dev-server equivalent of that for a single local process.
  const startupScript =
    `from app import app\n` +
    `app.run(host="127.0.0.1", port=${cfg.port}, debug=False, use_reloader=False, threaded=True)`;

  console.log(`[e2e] Starting Flask on ${cfg.baseURL} ...`);
  const logFd = fs.openSync(cfg.serverLogFile, "w");
  const server = spawn(cfg.pythonExecutable, ["-c", startupScript], {
    cwd: cfg.projectRoot,
    env: sharedEnv,
    stdio: ["ignore", logFd, logFd],
    detached: process.platform !== "win32",
  });
  fs.writeFileSync(cfg.serverPidFile, String(server.pid));
  server.unref();

  try {
    await waitUntilReady(cfg.baseURL, READY_TIMEOUT_MS);
  } catch (error) {
    console.error(fs.readFileSync(cfg.serverLogFile, "utf-8"));
    throw error;
  }
  console.log("[e2e] Flask is up.");

  await saveAdminStorageState(cfg);
  console.log(`[e2e] Admin session saved to ${cfg.adminStorageStatePath}.`);
}

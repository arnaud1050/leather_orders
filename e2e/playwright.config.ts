import { defineConfig, devices } from "@playwright/test";
import { resolveConfig } from "./lib/env";

const cfg = resolveConfig();

/**
 * Env vars this file (and the fixtures/pages built on it) respond to —
 * see .env.example for the full list with defaults:
 *
 *   E2E_BASE_URL       point at an already-running instance instead of
 *                      auto-starting one (skips seeding/server management
 *                      entirely — see global-setup.ts)
 *   E2E_PORT           port for the auto-started Flask server (default 5000)
 *   E2E_DB_PATH        scratch SQLite file path (default e2e/.tmp/e2e_test.db)
 *   E2E_SEED_DATA_FILE alternate fixture JSON (default e2e/seed/e2e-data.json)
 *   E2E_KEEP_DB        skip the post-run DB cleanup, for debugging
 *   PYTHON_EXECUTABLE  override the venv python resolved automatically
 *   E2E_HEADLESS       "false" to watch the browser (default true)
 *   E2E_VIDEO          "on" | "off" | "retain-on-failure" (default retain-on-failure)
 *   E2E_VIEWPORT_WIDTH / E2E_VIEWPORT_HEIGHT   (default 1280x800)
 *   E2E_WORKERS         cap on parallel workers (default 4 — see the note
 *                       on `workers` below before raising it)
 *
 * Pick a browser with Playwright's own --project flag, not an env var:
 *   npx playwright test --project=firefox
 */
const headless = process.env.E2E_HEADLESS !== "false";
const video = (process.env.E2E_VIDEO as "on" | "off" | "retain-on-failure") || "retain-on-failure";
const viewport = {
  width: parseInt(process.env.E2E_VIEWPORT_WIDTH || "1280", 10),
  height: parseInt(process.env.E2E_VIEWPORT_HEIGHT || "800", 10),
};

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // Capped rather than left to Playwright's own CPU-core auto-detection:
  // the app is served by Flask's dev server (threaded, but still one
  // Python process — see global-setup.ts's threaded=True comment), and
  // pushing many browser contexts at it concurrently produces real
  // page.goto timeouts, not flakiness in the tests themselves — confirmed
  // by the same run being 100% reliable at 2 workers and inconsistent at
  // 8. Raise E2E_WORKERS if your machine (and whatever it's serving the
  // app with) can sustain more; production's gunicorn setup with real
  // worker processes doesn't have this ceiling.
  workers: process.env.CI ? 2 : parseInt(process.env.E2E_WORKERS || "4", 10),
  globalSetup: require.resolve("./global-setup"),
  globalTeardown: require.resolve("./global-teardown"),
  timeout: 30_000,
  expect: { timeout: 5_000 },

  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
    ["allure-playwright", { resultsDir: "allure-results", detail: true, suiteTitle: true }],
  ],

  use: {
    baseURL: cfg.baseURL,
    headless,
    viewport,
    video,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    actionTimeout: 10_000,
    navigationTimeout: 20_000,
  },

  projects: [
    // mobile-nav.spec.ts asserts on CO5a's <680px breakpoint specifically,
    // so it's excluded from the desktop-viewport projects (it would just
    // find the hamburger toggle hidden there, which is correct app
    // behaviour but the wrong thing for that test to hit) and is the only
    // file mobile-chromium runs.
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport }, testIgnore: /mobile-nav\.spec\.ts/ },
    { name: "firefox", use: { ...devices["Desktop Firefox"], viewport }, testIgnore: /mobile-nav\.spec\.ts/ },
    { name: "webkit", use: { ...devices["Desktop Safari"], viewport }, testIgnore: /mobile-nav\.spec\.ts/ },
    {
      name: "mobile-chromium",
      use: { ...devices["Pixel 7"] },
      testMatch: /mobile-nav\.spec\.ts/,
    },
  ],
});

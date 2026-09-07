/**
 * Custom `test` that hands specs an already-authenticated `Page`, instead
 * of every spec re-typing a login form submission. Import `test`/`expect`
 * from *this* file in spec files, not from `@playwright/test` directly:
 *
 *   import { test, expect } from "../fixtures/auth.fixture";
 *
 *   test("shows the timeline", async ({ adminPage }) => {
 *     await adminPage.goto("/");
 *   });
 */
import { test as base, expect } from "@playwright/test";
import * as fs from "fs";
import { resolveConfig } from "../lib/env";
import { LoginPage } from "../pages/LoginPage";
import { testData } from "./testData";

interface AuthFixtures {
  /**
   * Logged in as the seeded E2E company's admin (must_change_password
   * already cleared by the seed script — see seed/seed_e2e_data.py).
   * Reuses the session global-setup.ts already established, so no login
   * round-trip happens per test — but each test still gets its own fresh
   * BrowserContext, so localStorage (which is what most Tier-1 timeline/
   * list filters live in) always starts empty. Don't share a context
   * across tests just to save a page load; that's exactly how one test's
   * leftover "hide tentative" filter ends up silently failing the next.
   */
  adminPage: import("@playwright/test").Page;

  /**
   * Logged in fresh (not from cached storageState) as the one seeded user
   * who still has must_change_password=True (CO4h) — see
   * seed/e2e-data.json's freshUser entry. There is exactly one such user
   * in the fixture data on purpose: a test that actually completes the
   * password-change flow changes that user's real password, which would
   * break any other test still expecting the seeded one. If you need a
   * second test exercising this flow, seed a second "fresh" user rather
   * than reusing this one.
   */
  freshUserPage: import("@playwright/test").Page;
}

export const test = base.extend<AuthFixtures>({
  adminPage: async ({ browser }, use) => {
    const cfg = resolveConfig();
    if (!fs.existsSync(cfg.adminStorageStatePath)) {
      throw new Error(
        `No saved admin session at ${cfg.adminStorageStatePath} — global-setup.ts should have created ` +
          "this before any test ran. Did you run tests through `npx playwright test` (which runs " +
          "globalSetup automatically), rather than some other way?"
      );
    }
    const context = await browser.newContext({ storageState: cfg.adminStorageStatePath });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },

  freshUserPage: async ({ browser }, use) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    const login = new LoginPage(page);
    await login.goto();
    await login.login(testData.freshUser.email, testData.freshUser.password);
    await use(page);
    await context.close();
  },
});

export { expect };

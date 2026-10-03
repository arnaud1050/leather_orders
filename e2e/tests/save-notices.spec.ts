import { Page } from "@playwright/test";
import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { IntegrationsPage } from "../pages/IntegrationsPage";
import { SettingsPage } from "../pages/SettingsPage";

/**
 * MOD7 + MOD8 — after a save, the page reopens on the message, and the
 * message is in the section whose button was pressed, in its colour.
 *
 * pytest covers where each message is rendered, for every kind of save.
 * What it can't see is the browser half:
 *
 * - **the scroll.** `stay-in-place.js` reopens the page scrolled to the
 *   message. REQUIREMENTS listed this as checked by hand only; it matters
 *   more now that messages sit mid-page instead of at the top.
 * - **the colour as drawn.** The route tests assert the class names; a
 *   broken or overridden rule in style.css would still pass them.
 *
 * Green is `--status-delivered` (#2f7d4f), red is `--day-today` (#c23b34).
 */

const GREEN = "rgb(47, 125, 79)";
const RED = "rgb(194, 59, 52)";

/** A rule address unique to this test and this browser project — the
 * projects share one database (see sender-rules.spec.ts). */
function addressFor(project: string, slug: string): string {
  return `${slug}@${project.toLowerCase()}.example.com`;
}

async function scrollY(page: Page): Promise<number> {
  return await page.evaluate(() => window.scrollY);
}

test.describe("Save messages — in their section, in view, in colour (MOD7, MOD8)", () => {
  test("a saved setting reopens the page on a green message in that section", async ({
    adminPage,
  }) => {
    await feature("Settings");
    await description(
      "Calendar sync sits halfway down Settings > Email/Calendar. Its Save must bring the page back " +
        "scrolled to a green message under that section's heading, not to the top of the page."
    );
    const page = new IntegrationsPage(adminPage);

    await page.goto();
    await page.saveCalendarSync();

    const notice = page.slot("calendar-sync").locator(".save-notice");
    await expect(notice).toHaveText("Sync settings saved.");
    await expect(notice).toBeInViewport();
    await expect(notice).toHaveCSS("border-left-color", GREEN);
    expect(await scrollY(adminPage), "the page should not have reopened at the top").toBeGreaterThan(0);
    // The only message on the page: nothing at the top as well.
    await expect(adminPage.locator(".save-notice")).toHaveCount(1);
    await expect(page.section("Calendar sync").locator(".save-notice")).toHaveCount(1);
  });

  test("a refused rule far down the page reopens on a red message in Automatic handling", async ({
    adminPage,
  }, testInfo) => {
    await feature("Settings");
    await description(
      "Automatic handling is the last section of a long page. A duplicate rule is refused, and the " +
        "page must reopen on the red message in that section."
    );
    const page = new IntegrationsPage(adminPage);
    const address = addressFor(testInfo.project.name, "duplicate");

    await page.goto();
    await page.addRule(address, "hide");
    try {
      await page.addRule(address, "hide");

      const notice = page.notice(/already/);
      await expect(notice).toBeVisible();
      await expect(notice).toBeInViewport();
      await expect(notice).toHaveCSS("border-left-color", RED);
      expect(await scrollY(adminPage)).toBeGreaterThan(0);
      await expect(adminPage.locator(".save-notice")).toHaveCount(1);
    } finally {
      await page.goto();
      await page.deleteRule(address);
    }
  });

  test("a refused duplicate on Settings > Orders is red, in Order types, in view", async ({
    adminPage,
  }) => {
    await feature("Settings");
    await description(
      "The case MOD7 named as its manual check. Every new company is seeded with a \"Custom Order\" " +
        "type, so adding it again is refused and writes nothing."
    );
    const settings = new SettingsPage(adminPage);

    await settings.gotoOrders();
    await settings.addOrderType("Custom Order");

    const notice = settings.slot("order-types").locator(".save-notice");
    await expect(notice).toContainText('An order type called "Custom Order" already exists.');
    await expect(notice).toBeInViewport();
    await expect(notice).toHaveCSS("border-left-color", RED);
    await expect(adminPage.locator(".save-notice")).toHaveCount(1);
  });
});

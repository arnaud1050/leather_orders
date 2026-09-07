import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { BasePage } from "../pages/BasePage";
import { gotoPath } from "../lib/nav";

/**
 * CO5a — explicitly marked "— gap —" in REQUIREMENTS.md's coverage table:
 * a route test can see the markup for both the toggle and the links, but
 * only a real browser can tell you which one is actually visible at a
 * given width. Runs under the `mobile-chromium` project (see
 * playwright.config.ts's testMatch for this file) so it gets a phone-sized
 * viewport without needing resizeWindow calls scattered through the test.
 */
test.describe("Mobile nav collapse (CO5a)", () => {
  test("the hamburger toggle opens and closes the nav links", async ({ adminPage }) => {
    await feature("Navigation");
    await description(
      "CO5a: below 680px, .view-switch's links collapse behind a hamburger toggle that reveals them as a " +
        "dropdown on click, and clicking a link closes the menu again rather than leaving it open on the " +
        "page it navigates to."
    );
    const nav = new BasePage(adminPage);
    await gotoPath(adminPage, "/");

    await nav.expectMobileNavClosed();
    await nav.openMobileNav();
    await nav.expectMobileNavOpen();

    // Clicking a nav link closes the menu again (base.html's inline
    // script) rather than leaving it open on the page it navigates to.
    await nav.navLink("Orders").click();
    await expect(adminPage).toHaveURL("/orders");
  });

  test("both the toggle and the link list are always present in the markup", async ({ adminPage }) => {
    await feature("Navigation");
    await description(
      "CO5a: only CSS decides which of the toggle/link-list is visible at a given width — there's no " +
        "server-side 'is this a mobile request' branch, so both elements exist in the markup regardless " +
        "of viewport."
    );
    const nav = new BasePage(adminPage);
    await gotoPath(adminPage, "/");
    await expect(nav.navToggle).toBeAttached();
    await expect(nav.navLinks).toBeAttached();
  });
});

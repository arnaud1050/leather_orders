import { Locator, Page, expect } from "@playwright/test";
import { step } from "allure-js-commons";

/**
 * Shared shell every authenticated page renders (base.html's `.view-switch`
 * nav, including the mobile hamburger collapse — CO5/CO5a). Page objects
 * for a specific view extend this rather than duplicating nav locators.
 *
 * Every action/assertion method wraps its body in an Allure `step()` so the
 * report reads like a narrative ("Log out" -> "Go to /orders" -> ...)
 * instead of a single opaque pass/fail per test. Plain locator builders
 * (like `navLink` below) are left unwrapped — they don't do anything by
 * themselves, so a step around one would just be noise with no timing or
 * outcome to show.
 */
export class BasePage {
  readonly page: Page;
  readonly navToggle: Locator;
  readonly navLinks: Locator;

  constructor(page: Page) {
    this.page = page;
    this.navToggle = page.locator("#nav-toggle");
    this.navLinks = page.locator("#view-switch-links");
  }

  navLink(label: string): Locator {
    return this.navLinks.getByRole("link", { name: label, exact: true });
  }

  async goToTimeline(): Promise<void> {
    await step("Go to Timeline via the nav", async () => {
      await this.navLink("Timeline").click();
    });
  }

  async goToOrders(): Promise<void> {
    await step("Go to Orders via the nav", async () => {
      // The Orders link's accessible name is just "Orders" — Clients/
      // Inventory/Settings carry badge text inside the same <a>, Orders
      // doesn't, so an exact match is safe here.
      await this.navLink("Orders").click();
    });
  }

  async goToClients(): Promise<void> {
    await step("Go to Clients via the nav", async () => {
      await this.navLinks.locator('a[href*="/clients"]').first().click();
    });
  }

  async logOut(): Promise<void> {
    await step("Log out", async () => {
      await this.navLinks.getByRole("link", { name: "Log out" }).click();
    });
  }

  /** Below 680px the links collapse behind #nav-toggle (CO5a). */
  async openMobileNav(): Promise<void> {
    await step("Open the mobile nav via the hamburger toggle", async () => {
      await this.navToggle.click();
    });
  }

  async expectMobileNavOpen(): Promise<void> {
    await step("Expect the mobile nav to be open", async () => {
      await expect(this.navLinks).toHaveClass(/is-open/);
      await expect(this.navToggle).toHaveAttribute("aria-expanded", "true");
    });
  }

  async expectMobileNavClosed(): Promise<void> {
    await step("Expect the mobile nav to be closed", async () => {
      await expect(this.navLinks).not.toHaveClass(/is-open/);
      await expect(this.navToggle).toHaveAttribute("aria-expanded", "false");
    });
  }
}

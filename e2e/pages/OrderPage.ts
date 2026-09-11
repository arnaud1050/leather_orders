import { Locator, Page, expect } from "@playwright/test";
import { step } from "allure-js-commons";
import { BasePage } from "./BasePage";
import { gotoPath } from "../lib/nav";

export type OrderTab = "Details" | "Materials" | "Billing";

/**
 * templates/order_page.html — the full order page (MOD4, OR3, OR4).
 * Three tabs over one template; the bare /orders/<id> URL stays on Details
 * for backward-link compatibility.
 */
export class OrderPage extends BasePage {
  readonly tabs: Locator;
  readonly itemInput: Locator;
  readonly startInput: Locator;
  readonly dueInput: Locator;
  readonly pickupDateInput: Locator;
  readonly notesInput: Locator;
  readonly saveButton: Locator;
  readonly heading: Locator;

  constructor(page: Page) {
    super(page);
    this.tabs = page.locator(".settings-nav");
    this.itemInput = page.locator('input[name="item"]');
    this.startInput = page.locator('input[name="start"]');
    this.dueInput = page.locator('input[name="due"]');
    this.pickupDateInput = page.locator('input[name="pickup_date"]');
    this.notesInput = page.locator('textarea[name="notes"]');
    this.saveButton = page.getByRole("button", { name: "Save changes" });
    this.heading = page.locator(".detail-header h1");
  }

  async saveChanges(): Promise<void> {
    await step("Save the Details form", async () => {
      await this.saveButton.click();
    });
  }

  /** The inline message under a Details field (OR13) — count 0 when it's fine. */
  fieldError(name: string): Locator {
    return this.page
      .locator("label", { has: this.page.locator(`[name="${name}"]`) })
      .locator(".field-error");
  }

  async goto(orderId: number | string, returnTo?: string): Promise<void> {
    const path = returnTo
      ? `/orders/${orderId}?return_to=${encodeURIComponent(returnTo)}`
      : `/orders/${orderId}`;
    await step(`Go to order ${orderId}'s page`, async () => {
      await gotoPath(this.page, path);
    });
  }

  tab(name: OrderTab): Locator {
    return this.tabs.getByRole("link", { name, exact: true });
  }

  async openTab(name: OrderTab): Promise<void> {
    await step(`Open the order page's "${name}" tab`, async () => {
      await this.tab(name).click();
    });
  }

  async expectActiveTab(name: OrderTab): Promise<void> {
    await step(`Expect "${name}" to be the active tab`, async () => {
      await expect(this.tab(name)).toHaveClass(/is-active/);
    });
  }

  async setPickupDate(isoDate: string): Promise<void> {
    await step(`Set the pickup date to ${isoDate}`, async () => {
      await this.pickupDateInput.fill(isoDate);
      await this.saveButton.click();
    });
  }
}

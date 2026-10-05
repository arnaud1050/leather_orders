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

  /** "← Back to …", worded for wherever the page was opened from (MOD5). */
  backLink(): Locator {
    return this.page.locator(".back-link");
  }

  // --- Billing tab ---------------------------------------------------------

  async gotoBilling(orderId: number | string): Promise<void> {
    await step(`Go to order ${orderId}'s Billing tab`, async () => {
      await gotoPath(this.page, `/orders/${orderId}/billing`);
    });
  }

  /** "Order total" and the like, off the line-totals block. */
  totalRow(label: string): Locator {
    // Exact: "Total" would also match inside "Subtotal".
    const exact = new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`);
    return this.page
      .locator(".line-totals p")
      .filter({ has: this.page.locator("span", { hasText: exact }) })
      .locator("strong");
  }

  /** The invoice's number, linking to it — only once one exists. */
  invoiceLink(): Locator {
    return this.page.locator(".detail-invoice .doc-list__label");
  }

  /** Creates the draft, which lands on the new invoice's own page. */
  async createInvoice(): Promise<number> {
    return await step("Create the invoice from the Billing tab", async () => {
      await this.page.getByRole("button", { name: "Create invoice" }).click();
      await this.page.waitForURL(/\/invoices\/\d+/, { waitUntil: "domcontentloaded" });
      return Number(new URL(this.page.url()).pathname.split("/")[2]);
    });
  }

  async addLine(description: string, unitPrice: number): Promise<void> {
    await step(`Add the line "${description}" at $${unitPrice.toFixed(2)}`, async () => {
      const form = this.page.locator(".detail-lines form.detail-inline-add");
      await form.locator('input[name="description"]').fill(description);
      await form.locator('input[name="unit_price"]').fill(unitPrice.toFixed(2));
      await form.getByRole("button", { name: "Add line" }).click();
      await expect(this.page.locator(".detail-lines .doc-list__label", { hasText: description })).toBeVisible();
    });
  }

  async addPayment(amount: number): Promise<void> {
    await step(`Record a $${amount.toFixed(2)} payment`, async () => {
      const form = this.page.locator("form.detail-payments__add");
      await form.locator('input[name="amount"]').fill(amount.toFixed(2));
      await form.getByRole("button", { name: "Add payment" }).click();
      await expect(
        this.page.locator(".detail-payments .doc-list__label", { hasText: `$${amount.toFixed(2)}` })
      ).toBeVisible();
    });
  }

  async setPickupDate(isoDate: string): Promise<void> {
    await step(`Set the pickup date to ${isoDate}`, async () => {
      await this.pickupDateInput.fill(isoDate);
      await this.saveButton.click();
    });
  }
}

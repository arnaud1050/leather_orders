import { Locator, Page, expect } from "@playwright/test";
import { step } from "allure-js-commons";
import { BasePage } from "./BasePage";
import { gotoPath } from "../lib/nav";

export type InvoiceStatus = "Draft" | "Sent" | "Void";

/**
 * billing/templates/billing/invoice_page.html — one invoice (billing §11).
 *
 * The document half (`.invoice-doc`) reads the frozen copy once the invoice
 * has left draft and the live figures before that (F1, F14); the controls
 * below it change the status, which is what freezes it (F2).
 */
export class InvoicePage extends BasePage {
  readonly doc: Locator;
  readonly number: Locator;
  readonly statusPill: Locator;
  readonly issuerRegistrations: Locator;
  readonly totals: Locator;
  readonly paymentInstructions: Locator;
  readonly paymentsReceived: Locator;
  readonly warnings: Locator;
  readonly statusSelect: Locator;
  readonly saveButton: Locator;
  readonly backLink: Locator;

  constructor(page: Page) {
    super(page);
    this.doc = page.locator(".invoice-doc");
    this.number = page.locator(".invoice-doc__number");
    this.statusPill = page.locator(".invoice-doc__ref .pill");
    this.issuerRegistrations = page.locator(".invoice-doc__issuer-reg");
    this.totals = page.locator(".invoice-doc__totals");
    this.paymentInstructions = page.locator(".invoice-doc__pay");
    this.paymentsReceived = page.locator(".invoice-doc__payments");
    this.warnings = page.locator(".invoice-admin .warning-note");
    this.statusSelect = page.locator('.invoice-admin select[name="status"]');
    this.saveButton = page.locator(".invoice-admin").getByRole("button", { name: "Save", exact: true });
    this.backLink = page.locator(".back-link");
  }

  async goto(invoiceId: number | string): Promise<void> {
    await step(`Go to invoice ${invoiceId}`, async () => {
      await gotoPath(this.page, `/invoices/${invoiceId}`);
    });
  }

  /** One totals row by its label: "Subtotal", "GST (5%)", "Total", "Balance due"... */
  totalRow(label: string): Locator {
    // Exact: "Total" is also inside "Subtotal".
    const exact = new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`);
    return this.totals.locator("p").filter({ has: this.page.locator("span", { hasText: exact }) });
  }

  async expectTotals(rows: Record<string, string>): Promise<void> {
    await step(`Expect the invoice totals ${JSON.stringify(rows)}`, async () => {
      for (const [label, amount] of Object.entries(rows)) {
        await expect(this.totalRow(label).locator("strong")).toHaveText(amount);
      }
    });
  }

  /** The tax rows' labels, in order: ["GST (5%)", "PST (7%)"]. */
  async taxLabels(): Promise<string[]> {
    const labels = await this.totals.locator("p > span").allInnerTexts();
    return labels.filter((l) => /\(\d/.test(l));
  }

  async setStatus(status: InvoiceStatus): Promise<void> {
    await step(`Set the invoice to ${status} and save`, async () => {
      await this.statusSelect.selectOption({ label: status });
      await this.saveButton.click();
      // The page comes back with what the save did (MOD8), which is also
      // proof the round trip finished before anything is read off it.
      await expect(this.page.locator(".save-notice", { hasText: `Invoice marked ${status.toLowerCase()}.` })).toBeVisible();
    });
  }

  async expectStatus(label: string): Promise<void> {
    await step(`Expect the invoice to read "${label}"`, async () => {
      await expect(this.statusPill).toHaveText(label);
    });
  }
}

/** billing/templates/billing/invoice_list.html — every invoice, plus the
 * "Not invoiced yet" to-do list (U8). */
export class InvoicesListPage extends BasePage {
  readonly table: Locator;
  readonly notInvoiced: Locator;

  constructor(page: Page) {
    super(page);
    this.table = page.locator(".ledger > .table-scroll table.data-table");
    this.notInvoiced = page.locator(".detail-uninvoiced");
  }

  async goto(): Promise<void> {
    await step("Go to /invoices", async () => {
      await gotoPath(this.page, "/invoices");
    });
  }

  rowByNumber(number: string): Locator {
    return this.table.locator("tbody tr").filter({ hasText: number });
  }

  notInvoicedEntry(item: string): Locator {
    return this.notInvoiced.locator("li").filter({ hasText: item });
  }
}

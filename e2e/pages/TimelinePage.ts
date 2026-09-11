import { Locator, Page, expect } from "@playwright/test";
import { step } from "allure-js-commons";
import { BasePage } from "./BasePage";
import { gotoPath } from "../lib/nav";

export type TimelineStatus = "tentative" | "confirmed" | "ready" | "delivered";
export type TimelineSort = "start" | "start_rush" | "due" | "due_rush" | "value";

/** templates/timeline.html — the default landing view (TL1-TL12). */
export class TimelinePage extends BasePage {
  readonly orderCount: Locator;
  readonly sortSelect: Locator;
  readonly newOrderButton: Locator;
  readonly prevLink: Locator;
  readonly nextLink: Locator;

  constructor(page: Page) {
    super(page);
    this.orderCount = page.locator("#order-count");
    this.sortSelect = page.locator("#timeline-sort");
    this.newOrderButton = page.getByRole("link", { name: "+ New order" });
    this.prevLink = page.locator('.ledger__nav a[title="Previous"]');
    this.nextLink = page.locator('.ledger__nav a[title="Next"]');
  }

  async goto(): Promise<void> {
    await step("Go to the Timeline", async () => {
      await gotoPath(this.page, "/");
    });
  }

  /** All non-header rows currently in the DOM, filtered or not. */
  rows(): Locator {
    return this.page.locator(".timeline__row:not(.timeline__row--header)");
  }

  visibleRows(): Locator {
    // `:visible` is a Playwright CSS extension — applied in the selector
    // itself, not chained via .locator(), since that would look for a
    // *descendant* rather than filter the rows themselves.
    return this.page.locator(".timeline__row:not(.timeline__row--header):visible");
  }

  rowByOrderItem(item: string): Locator {
    return this.rows().filter({ has: this.page.locator(".timeline__bar-label", { hasText: item }) });
  }

  legendButton(status: TimelineStatus): Locator {
    return this.page.locator(`#status-legend .legend__item[data-status="${status}"]`);
  }

  async toggleStatusFilter(status: TimelineStatus): Promise<void> {
    await step(`Toggle the '${status}' status filter`, async () => {
      await this.legendButton(status).click();
    });
  }

  async expectStatusFilteredOff(status: TimelineStatus): Promise<void> {
    await step(`Expect '${status}' to be filtered off`, async () => {
      await expect(this.legendButton(status)).toHaveClass(/legend__item--off/);
    });
  }

  async expectStatusFilteredOn(status: TimelineStatus): Promise<void> {
    await step(`Expect '${status}' to be filtered on`, async () => {
      await expect(this.legendButton(status)).not.toHaveClass(/legend__item--off/);
    });
  }

  async setSort(sort: TimelineSort): Promise<void> {
    await step(`Set the timeline sort to '${sort}'`, async () => {
      await this.sortSelect.selectOption(sort);
    });
  }

  async openClientModal(clientId: number | string): Promise<Locator> {
    return await step(`Open the client modal for client ${clientId}`, async () => {
      await this.page.locator(`[data-open-client="${clientId}"]`).click();
      const dialog = this.page.locator(`#client-modal-${clientId}`);
      await expect(dialog).toBeVisible();
      return dialog;
    });
  }

  async openOrderModalByItem(item: string): Promise<Locator> {
    return await step(`Open the order modal for '${item}'`, async () => {
      const bar = this.page.locator(`.timeline__bar[title^="${item}"]`).first();
      const orderId = await bar.getAttribute("data-open-order");
      await bar.click();
      const dialog = this.page.locator(`#order-modal-${orderId}`);
      await expect(dialog).toBeVisible();
      return dialog;
    });
  }

  /** An order's dialog, found without clicking anything — for one the page
   * reopened by itself after a refused quick edit (OR13). */
  async orderDialogByItem(item: string): Promise<Locator> {
    return await step(`Find the order dialog for '${item}'`, async () => {
      const bar = this.page.locator(`.timeline__bar[title^="${item}"]`).first();
      const orderId = await bar.getAttribute("data-open-order");
      return this.page.locator(`#order-modal-${orderId}`);
    });
  }

  /** The inline message under a field inside a dialog — count 0 when it's fine. */
  dialogFieldError(dialog: Locator, name: string): Locator {
    return dialog
      .locator("label", { has: this.page.locator(`[name="${name}"]`) })
      .locator(".field-error");
  }

  async closeModal(dialog: Locator): Promise<void> {
    await step("Close the modal", async () => {
      await dialog.locator("[data-close-modal]").click();
      await expect(dialog).toBeHidden();
    });
  }

  async goPrevWindow(): Promise<void> {
    await step("Go to the previous timeline window", async () => {
      await this.prevLink.click();
    });
  }

  async goNextWindow(): Promise<void> {
    await step("Go to the next timeline window", async () => {
      await this.nextLink.click();
    });
  }
}

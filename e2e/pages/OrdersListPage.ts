import { Locator, Page, expect } from "@playwright/test";
import { step } from "allure-js-commons";
import { BasePage } from "./BasePage";
import { gotoPath } from "../lib/nav";

export type OrderStatus = "tentative" | "confirmed" | "ready" | "delivered" | "cancelled";

/** templates/orders_list.html — /orders (LST1-LST12). */
export class OrdersListPage extends BasePage {
  readonly table: Locator;
  readonly orderCount: Locator;
  readonly balanceTotal: Locator;

  constructor(page: Page) {
    super(page);
    this.table = page.locator("#orders-table");
    this.orderCount = page.locator("#order-count");
    this.balanceTotal = page.locator("#order-balance-total");
  }

  async goto(): Promise<void> {
    await step("Go to /orders", async () => {
      await gotoPath(this.page, "/orders");
    });
  }

  rowByItem(item: string): Locator {
    return this.table.locator("tbody tr").filter({ hasText: item });
  }

  legendButton(status: OrderStatus): Locator {
    return this.page.locator(`#status-legend .legend__item[data-status="${status}"]`);
  }

  async toggleStatusFilter(status: OrderStatus): Promise<void> {
    await step(`Toggle the '${status}' status filter`, async () => {
      await this.legendButton(status).click();
    });
  }

  async sortByColumn(label: string): Promise<void> {
    await step(`Sort by the '${label}' column`, async () => {
      await this.table.locator("thead a.sort-link", { hasText: label }).click();
    });
  }

  async columnValues(columnIndex: number): Promise<string[]> {
    return await step(`Read column ${columnIndex}'s values`, async () => {
      return this.table.locator(`tbody tr td:nth-child(${columnIndex + 1})`).allTextContents();
    });
  }
}

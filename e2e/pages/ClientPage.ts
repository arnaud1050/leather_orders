import { Locator, Page, expect } from "@playwright/test";
import { step } from "allure-js-commons";
import { BasePage } from "./BasePage";
import { gotoPath } from "../lib/nav";

export type ClientTab = "Information" | "Orders" | "Emails";

/**
 * templates/client_page.html — the full client profile (MOD3, CL15, CL17).
 * Tabs are real routes sharing one template, switched on a `section` var,
 * and every tab link carries `return_to` through (MOD5).
 */
export class ClientPage extends BasePage {
  readonly tabs: Locator;
  readonly priorOrderCountInput: Locator;
  readonly hideButton: Locator;
  readonly hideDialog: Locator;
  readonly showButton: Locator;
  readonly ordersTable: Locator;

  constructor(page: Page) {
    super(page);
    this.tabs = page.locator(".settings-nav");
    this.priorOrderCountInput = page.locator('input[name="prior_order_count"]');
    this.hideButton = page.locator('[data-open-lifecycle="hide-client-modal"]');
    this.hideDialog = page.locator("#hide-client-modal");
    this.showButton = page.getByRole("button", { name: "Show on the client list" });
    this.ordersTable = page.locator("table.invoice-table");
  }

  async goto(clientId: number | string, returnTo?: string): Promise<void> {
    const path = returnTo
      ? `/clients/${clientId}?return_to=${encodeURIComponent(returnTo)}`
      : `/clients/${clientId}`;
    await step(`Go to client ${clientId}'s page`, async () => {
      await gotoPath(this.page, path);
    });
  }

  tab(name: ClientTab): Locator {
    return this.tabs.getByRole("link", { name, exact: true });
  }

  async openTab(name: ClientTab): Promise<void> {
    await step(`Open the client page's "${name}" tab`, async () => {
      await this.tab(name).click();
    });
  }

  async expectActiveTab(name: ClientTab): Promise<void> {
    await step(`Expect "${name}" to be the active tab`, async () => {
      await expect(this.tab(name)).toHaveClass(/is-active/);
    });
  }

  /** CL17 — hiding is behind a confirm dialog that spells out what it won't touch. */
  async hide(): Promise<void> {
    await step("Hide this client (via the confirm dialog)", async () => {
      await this.hideButton.click();
      await expect(this.hideDialog).toBeVisible();
      await this.hideDialog.getByRole("button", { name: "Hide", exact: true }).click();
    });
  }

  async unhide(): Promise<void> {
    await step("Put this client back on the list", async () => {
      await this.showButton.click();
    });
  }

  async setPriorOrderCount(value: string): Promise<void> {
    await step(`Set "Orders not in the system" to ${value}`, async () => {
      await this.priorOrderCountInput.fill(value);
      await this.page.getByRole("button", { name: "Save" }).first().click();
    });
  }
}

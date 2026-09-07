import { Locator, Page } from "@playwright/test";
import { step } from "allure-js-commons";
import { BasePage } from "./BasePage";
import { gotoPath } from "../lib/nav";

export type OrderGroup = "with" | "none";

/** templates/clients_list.html — /clients (LST1, LST5, CL19). */
export class ClientsListPage extends BasePage {
  readonly table: Locator;
  readonly clientCount: Locator;
  readonly returningCount: Locator;
  readonly addClientButton: Locator;

  constructor(page: Page) {
    super(page);
    this.table = page.locator("#clients-table");
    this.clientCount = page.locator("#client-count");
    this.returningCount = page.locator("#client-returning-count");
    this.addClientButton = page.getByRole("link", { name: "+ Add client" });
  }

  async goto(showHidden = false): Promise<void> {
    await step(showHidden ? "Go to /clients?hidden=1 (the archive)" : "Go to /clients", async () => {
      await gotoPath(this.page, showHidden ? "/clients?hidden=1" : "/clients");
    });
  }

  rowByName(name: string): Locator {
    return this.table.locator("tbody tr").filter({ hasText: name });
  }

  legendButton(group: OrderGroup): Locator {
    return this.page.locator(`#orders-legend .legend__item[data-group="${group}"]`);
  }

  async toggleOrderGroupFilter(group: OrderGroup): Promise<void> {
    await step(`Toggle the '${group}' orders filter`, async () => {
      await this.legendButton(group).click();
    });
  }

  archiveLink(): Locator {
    return this.page.locator('a.legend__aside[href*="hidden=1"]');
  }
}

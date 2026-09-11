import { Locator, Page } from "@playwright/test";
import { step } from "allure-js-commons";
import { BasePage } from "./BasePage";
import { gotoPath } from "../lib/nav";

/**
 * templates/new_order.html — /orders/new (OR9-OR12, OR13).
 *
 * A save the server refuses re-renders this same form in place, with a
 * `.field-error` sentence inside each bad field's `<label>` and every value
 * that was typed still filled in.
 */
export class NewOrderPage extends BasePage {
  readonly clientSelect: Locator;
  readonly newClientFields: Locator;
  readonly newFirstNameInput: Locator;
  readonly newLastNameInput: Locator;
  readonly newEmailInput: Locator;
  readonly itemInput: Locator;
  readonly startInput: Locator;
  readonly dueInput: Locator;
  readonly notesInput: Locator;
  readonly submitButton: Locator;

  constructor(page: Page) {
    super(page);
    this.clientSelect = page.locator('select[name="client_id"]');
    this.newClientFields = page.locator("#new-client-fields");
    this.newFirstNameInput = page.locator('input[name="new_first_name"]');
    this.newLastNameInput = page.locator('input[name="new_last_name"]');
    this.newEmailInput = page.locator('input[name="new_email"]');
    this.itemInput = page.locator('input[name="item"]');
    this.startInput = page.locator('input[name="start"]');
    this.dueInput = page.locator('input[name="due"]');
    this.notesInput = page.locator('textarea[name="notes"]');
    this.submitButton = page.getByRole("button", { name: "Create order" });
  }

  async goto(returnTo = "/orders"): Promise<void> {
    await step("Go to the new-order form", async () => {
      await gotoPath(this.page, `/orders/new?return_to=${encodeURIComponent(returnTo)}`);
    });
  }

  async selectClient(name: string): Promise<void> {
    await step(`Choose the client '${name}'`, async () => {
      await this.clientSelect.selectOption({ label: name });
    });
  }

  async chooseNewClient(): Promise<void> {
    await step("Choose '+ Add new client'", async () => {
      await this.clientSelect.selectOption({ value: "new" });
    });
  }

  async submit(): Promise<void> {
    await step("Submit the new-order form", async () => {
      await this.submitButton.click();
    });
  }

  /** The inline message under a field — empty (count 0) when it's fine. */
  fieldError(name: string): Locator {
    return this.page
      .locator("label", { has: this.page.locator(`[name="${name}"]`) })
      .locator(".field-error");
  }
}

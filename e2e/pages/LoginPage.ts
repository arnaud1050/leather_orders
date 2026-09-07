import { Locator, Page, expect } from "@playwright/test";
import { step } from "allure-js-commons";
import { gotoPath } from "../lib/nav";

/** templates/login.html — the only unauthenticated view besides /privacy and /terms (CO4). */
export class LoginPage {
  readonly page: Page;
  readonly emailInput: Locator;
  readonly passwordInput: Locator;
  readonly submitButton: Locator;
  readonly errorMessage: Locator;

  constructor(page: Page) {
    this.page = page;
    this.emailInput = page.locator('input[name="email"]');
    this.passwordInput = page.locator('input[name="password"]');
    this.submitButton = page.getByRole("button", { name: "Sign in" });
    this.errorMessage = page.locator(".login-error");
  }

  async goto(next?: string): Promise<void> {
    await step(next ? `Go to /login?next=${next}` : "Go to /login", async () => {
      await gotoPath(this.page, next ? `/login?next=${encodeURIComponent(next)}` : "/login");
    });
  }

  async login(email: string, password: string): Promise<void> {
    // The step name carries the email only — never the password, even
    // though this is a throwaway test account, so nobody has to think
    // twice about whether an Allure report is safe to share.
    await step(`Log in as ${email}`, async () => {
      await this.emailInput.fill(email);
      await this.passwordInput.fill(password);
      await this.submitButton.click();
    });
  }

  async expectError(message?: string): Promise<void> {
    await step("Expect a login error message", async () => {
      await expect(this.errorMessage).toBeVisible();
      if (message) await expect(this.errorMessage).toContainText(message);
    });
  }
}

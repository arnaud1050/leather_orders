import { Locator, Page } from "@playwright/test";
import { step } from "allure-js-commons";
import { BasePage } from "./BasePage";
import { gotoPath } from "../lib/nav";

/**
 * Settings → Email/Calendar (`/settings/integrations`), specifically the
 * "Automatic handling" section: the sender rules and their contact-form
 * field mappings (`communications/REQUIREMENTS.md` R-*, F-*).
 *
 * The rules section renders with no mailbox connected, so nothing here
 * needs OAuth or a seeded EmailAccount — which is what makes it testable
 * in a browser at all.
 *
 * Rules are located by the value of their address input rather than by
 * position, because this spec shares one database with every other spec
 * *and* with the same file running under three browser projects at once
 * (`fullyParallel: true`). Nothing may assume it's looking at the only
 * rule on the page.
 */
export class IntegrationsPage extends BasePage {
  /** The add-rule form, matched on its exact action so it isn't confused
   * with the per-rule edit forms, which post to `/integrations/rules/<id>`
   * and carry an input of the same name. */
  readonly addRuleForm: Locator;

  constructor(page: Page) {
    super(page);
    this.addRuleForm = page.locator('form[action="/integrations/rules"]');
  }

  async goto(): Promise<void> {
    await step("Go to Settings > Email/Calendar", async () => {
      await gotoPath(this.page, "/settings/integrations");
    });
  }

  /** The whole card for a convert rule — address row, mappings, add-field
   * form. Hide rules are a plain `<li>`; see `hideRuleRow`. */
  ruleCard(pattern: string): Locator {
    return this.page
      .locator(".rule-card")
      .filter({ has: this.page.locator(`input[name="pattern"][value="${pattern}"]`) });
  }

  hideRuleRow(pattern: string): Locator {
    return this.page
      .locator(".settings-source-list__item")
      .filter({ has: this.page.locator(`input[name="pattern"][value="${pattern}"]`) });
  }

  /** The address input / Save / Delete row, for either kind of rule. */
  editRow(pattern: string): Locator {
    return this.page
      .locator(".rule-card__head, .settings-source-list__item")
      .filter({ has: this.page.locator(`input[name="pattern"][value="${pattern}"]`) });
  }

  async addRule(pattern: string, action: "convert" | "hide"): Promise<void> {
    await step(`Add a '${action}' rule for ${pattern}`, async () => {
      await this.addRuleForm.locator('input[name="pattern"]').fill(pattern);
      await this.addRuleForm.locator('select[name="action"]').selectOption(action);
      await this.addRuleForm.getByRole("button", { name: "Add rule" }).click();
    });
  }

  async deleteRule(pattern: string): Promise<void> {
    await step(`Delete the rule for ${pattern}`, async () => {
      await this.editRow(pattern).getByRole("button", { name: "Delete" }).click();
    });
  }

  async changeAddress(from: string, to: string): Promise<void> {
    await step(`Change the rule's address from ${from} to ${to}`, async () => {
      const row = this.editRow(from);
      await row.locator('input[name="pattern"]').fill(to);
      await row.getByRole("button", { name: "Save" }).click();
    });
  }

  /** The field-mapping form inside one convert rule's card. */
  addFieldForm(pattern: string): Locator {
    return this.ruleCard(pattern).locator("form").filter({
      has: this.page.locator('select[name="target"]'),
    });
  }

  targetSelect(pattern: string): Locator {
    return this.addFieldForm(pattern).locator('select[name="target"]');
  }

  async addMapping(pattern: string, label: string, target: string): Promise<void> {
    await step(`Map "${label}" to ${target}`, async () => {
      const form = this.addFieldForm(pattern);
      await form.locator('input[name="label"]').fill(label);
      await form.locator('select[name="target"]').selectOption(target);
      await form.getByRole("button", { name: "Add field" }).click();
    });
  }

  /**
   * "Label → Target" for every mapping currently listed on a rule.
   *
   * Normalised around the arrow, which is a `<span>` spaced by CSS margins
   * rather than by whitespace in the markup — so `innerText` reads
   * "City→City". Re-spacing it here keeps the assertions in the spec
   * readable and independent of how a given browser renders the gap.
   */
  async mappingLabels(pattern: string): Promise<string[]> {
    return await step("Read the rule's field mappings", async () => {
      const raw = await this.ruleCard(pattern)
        .locator(".settings-source-list__item .settings-source-list__label")
        .allInnerTexts();
      return raw.map((text) =>
        text
          .split("→")
          .map((part) => part.trim())
          .join(" → ")
      );
    });
  }

  /** A flash message the module's one-shot notice rendered at the top. */
  notice(text: string | RegExp): Locator {
    return this.page.locator(".warning-note, .detail-note").filter({ hasText: text });
  }

}

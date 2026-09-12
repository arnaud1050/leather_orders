import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { IntegrationsPage } from "../pages/IntegrationsPage";

/**
 * Sender rules and their contact-form field mapping
 * (`communications/REQUIREMENTS.md` R-21 … R-27, F-23).
 *
 * pytest already covers the *behaviour* here thoroughly — the conflict
 * refusal, the pattern cleaning, the mappings surviving an edit — through
 * the real routes. None of that is repeated for its own sake. What's left
 * is the part a route test structurally can't see:
 *
 * - **The grouped picker's structure.** The `<optgroup>`s are opened and
 *   closed by separate `{% if group %}` conditionals in the template, so a
 *   mismatched pair would produce invalid HTML that still contains every
 *   option — `'value="street"' in body` passes either way. Only a real
 *   DOM parse can say the Address options are actually *inside* the
 *   Address group, and that Ignore is outside every group.
 * - **`required` on the address input**, which is browser-enforced: the
 *   server's own guard against a blank pattern is a different code path,
 *   and reaching it means the form submitted, which is the thing that
 *   shouldn't happen.
 * - **The forms being wired to the right endpoints.** A convert rule's card
 *   carries three forms — edit, delete, add-field — and the first two hold
 *   an input of the same name, posting to `/integrations/rules/<id>` and
 *   `/integrations/rules/<id>/delete`. Driving them as a person does is
 *   what proves Save saves and Delete deletes.
 *
 * **Deliberately not here: a layout assertion.** An earlier draft checked
 * the address input, Save and Delete shared a line and that the page never
 * scrolled sideways. It passed with the flex-basis tripled *and* with the
 * whole `.rule-edit__pattern` rule deleted — three controls in a flex row
 * simply don't wrap at any width the app renders at. A test that cannot
 * fail is worse than no test, so it was removed rather than kept for the
 * look of it. If this row ever gains a fourth control, that changes.
 *
 * Every rule this file creates carries the project name in its address.
 * `fullyParallel: true` means these tests run concurrently, and the same
 * file runs under chromium, firefox and webkit against **one** shared
 * database — a fixed pattern would collide on the unique
 * (company_id, pattern) constraint the moment two projects overlap.
 */

/** A rule address unique to this test and this browser project. */
function addressFor(project: string, slug: string): string {
  return `${slug}@${project.toLowerCase()}.example.com`;
}

test.describe("Sender rules — the edit row (R-21, R-27)", () => {
  test("an emptied address is refused by the browser before it ever posts", async ({
    adminPage,
  }, testInfo) => {
    await feature("Settings");
    await description(
      "R-22: the address input is `required`, so clearing it and pressing Save is blocked client-side " +
        "and the rule is never touched. The server's own guard against a blank pattern is a different " +
        "code path, already covered by pytest — this is the half only a browser performs."
    );
    const page = new IntegrationsPage(adminPage);
    const address = addressFor(testInfo.project.name, "required");

    await page.goto();
    await page.addRule(address, "hide");

    try {
      const row = page.editRow(address);
      await row.locator('input[name="pattern"]').fill("");
      await row.getByRole("button", { name: "Save" }).click();

      // Still on the settings page, and the rule still has its address:
      // the form never submitted.
      await expect(adminPage).toHaveURL(/\/settings\/integrations/);
      await expect(
        row.locator('input[name="pattern"]')
      ).toHaveJSProperty("validity.valueMissing", true);

      await page.goto();
      await expect(page.hideRuleRow(address)).toBeVisible();
    } finally {
      await page.goto();
      await page.deleteRule(address);
    }
  });
});

test.describe("Sender rules — the field-mapping picker (F-23, R-24)", () => {
  test("the address targets really are inside the Address group", async ({
    adminPage,
  }, testInfo) => {
    await feature("Settings");
    await description(
      "F-23: the picker's <optgroup>s are opened and closed by separate `{% if group %}` conditionals, " +
        "so a mismatched pair renders invalid HTML that still contains every option — a substring check " +
        "on the response body passes either way. Only a parsed DOM can say the Address options are " +
        "nested inside the Address group, and that Ignore is deliberately outside every group."
    );
    const page = new IntegrationsPage(adminPage);
    const address = addressFor(testInfo.project.name, "picker");

    await page.goto();
    await page.addRule(address, "convert");

    try {
      const select = page.targetSelect(address);

      const addressGroup = select.locator('optgroup[label="Address"] option');
      await expect(addressGroup).toHaveCount(4);
      expect(await addressGroup.evaluateAll((os) =>
        os.map((o) => (o as HTMLOptionElement).value)
      )).toEqual(["street", "city", "province", "postal_code"]);

      // Ignore isn't a destination, so it sits outside every group. Asserted
      // on the attribute rather than with toHaveValue, which is for the
      // form control itself — on a bare <option> it reads the enclosing
      // select's current selection instead.
      await expect(select.locator("> option")).toHaveCount(1);
      await expect(select.locator("> option")).toHaveAttribute("value", "ignore");

      // Every group the template defines, in order.
      expect(
        await select.locator("optgroup").evaluateAll((gs) =>
          gs.map((g) => (g as HTMLOptGroupElement).label)
        )
      ).toEqual(["Name", "Contact", "Address", "The enquiry"]);
    } finally {
      await page.goto();
      await page.deleteRule(address);
    }
  });

  test("mapping a label through the real picker, then hitting the one-per-target rule", async ({
    adminPage,
  }, testInfo) => {
    await feature("Settings");
    await description(
      "R-24: choosing a grouped option and submitting it stores the mapping, and a second label aimed " +
        "at the same field is refused with a message naming the mapping in the way. pytest covers the " +
        "refusal itself; what a browser adds is that the grouped <select> is a working control — the " +
        "option is selectable and its value survives the round trip."
    );
    const page = new IntegrationsPage(adminPage);
    const address = addressFor(testInfo.project.name, "conflict");

    await page.goto();
    await page.addRule(address, "convert");

    try {
      await page.addMapping(address, "City", "city");
      expect(await page.mappingLabels(address)).toEqual(["City → City"]);

      // A second label aimed at the same field is refused, by name.
      await page.addMapping(address, "Town", "city");
      await expect(page.notice(/already fills/)).toBeVisible();
      await expect(page.notice(/"City"/)).toBeVisible();
      expect(
        await page.mappingLabels(address),
        "the refused mapping must not have been stored"
      ).toEqual(["City → City"]);

      // Ignore is the one repeatable target (R-24), so these both stick.
      await page.addMapping(address, "File Upload", "ignore");
      await page.addMapping(address, "Captcha", "ignore");
      expect((await page.mappingLabels(address)).length).toBe(3);
    } finally {
      await page.goto();
      await page.deleteRule(address);
    }
  });
});

test.describe("Sender rules — editing an address (R-21)", () => {
  test("changing the address keeps the field mappings", async ({ adminPage }, testInfo) => {
    await feature("Settings");
    await description(
      "R-21: the whole reason the address is editable rather than delete-and-re-add. Driven through the " +
        "real form rather than a POST, so it also covers the edit form being wired to the right action " +
        "— a card carries two forms posting to different endpoints with an input of the same name."
    );
    const page = new IntegrationsPage(adminPage);
    const before = addressFor(testInfo.project.name, "rename-before");
    const after = addressFor(testInfo.project.name, "rename-after");

    await page.goto();
    await page.addRule(before, "convert");
    await page.addMapping(before, "Name", "name");
    await page.addMapping(before, "Email", "email");

    let renamed = false;
    try {
      await page.changeAddress(before, after);
      renamed = true;

      await expect(page.ruleCard(after)).toBeVisible();
      await expect(page.ruleCard(before)).toHaveCount(0);
      expect(await page.mappingLabels(after)).toEqual([
        "Name → Full name (split into first and last)",
        "Email → Email address",
      ]);

      // And it's the stored rule that changed, not just the input's value.
      await page.goto();
      expect((await page.mappingLabels(after)).length).toBe(2);
    } finally {
      await page.goto();
      await page.deleteRule(renamed ? after : before);
    }
  });
});

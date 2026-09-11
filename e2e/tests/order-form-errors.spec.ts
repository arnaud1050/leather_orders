import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { NewOrderPage } from "../pages/NewOrderPage";
import { OrderPage } from "../pages/OrderPage";
import { TimelinePage } from "../pages/TimelinePage";
import { clientByKey, clientFullName, isoDateOffset } from "../fixtures/testData";
import { gotoPath } from "../lib/nav";

/**
 * OR13 — a save the server refuses explains itself under the field at fault
 * and gives back everything that was typed. The route tests prove the HTML
 * carries the message; these prove a person actually sees it where they're
 * looking, and — for the timeline — that the dialog they were typing in
 * comes back open, which is JavaScript no route test can run.
 *
 * Every test here stops at a refused submission, and a refused submission
 * writes nothing. So they can share the seeded orders with the rest of the
 * suite, running in parallel, without disturbing anyone's assertions.
 */

/** YYYY-MM-DD shifted by `days`, in UTC so it can't wander across midnight. */
function shiftIso(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

test.describe("New order form — refused saves explain themselves (OR2a, OR12, OR13)", () => {
  test("a name that's only spaces and a backwards due date are both explained, and nothing typed is lost", async ({
    adminPage,
  }) => {
    await feature("Orders");
    await description(
      "OR12/OR2a/OR13: `required` on the Item field is satisfied by spaces, and the two date pickers " +
        "have no relationship to each other — so both mistakes reach the server from an ordinary " +
        "browser. Each must come back as a sentence under its own field, and the form must come back " +
        "with the client, dates and notes exactly as they were typed."
    );
    const form = new NewOrderPage(adminPage);
    const hedy = clientFullName(clientByKey("hedy"));
    const start = isoDateOffset(20);
    const due = isoDateOffset(5);

    await form.goto();
    await form.selectClient(hedy);
    await form.itemInput.fill("   ");
    await form.startInput.fill(start);
    await form.dueInput.fill(due);
    await form.notesInput.fill("Wants brass hardware");
    await form.submit();

    await expect(form.fieldError("item")).toContainText("can't be blank or only spaces");
    await expect(form.fieldError("due")).toContainText("is before the start date");
    await expect(form.itemInput).toHaveAttribute("aria-invalid", "true");
    await expect(form.dueInput).toHaveAttribute("aria-invalid", "true");

    // Only the fields at fault say anything.
    await expect(form.fieldError("start")).toHaveCount(0);

    // Everything typed survives the round trip.
    await expect(form.clientSelect.locator("option:checked")).toHaveText(hedy);
    await expect(form.startInput).toHaveValue(start);
    await expect(form.dueInput).toHaveValue(due);
    await expect(form.notesInput).toHaveValue("Wants brass hardware");
  });

  test("'+ Add new client' with its names left as spaces comes back open, explained, and filled in", async ({
    adminPage,
  }) => {
    await feature("Orders");
    await description(
      "OR11/OR13: the new-client fields are revealed by the page's own script. A refused save has to " +
        "bring them back *revealed* — the choice re-selected server-side, the script re-running on " +
        "load — or the messages would be sitting inside a hidden fieldset where nobody can see them."
    );
    const form = new NewOrderPage(adminPage);

    await form.goto();
    await form.chooseNewClient();
    await form.newFirstNameInput.fill("  ");
    await form.newLastNameInput.fill("  ");
    await form.newEmailInput.fill("new.client@example.invalid");
    await form.itemInput.fill("Card holder");
    await form.startInput.fill(isoDateOffset(1));
    await form.dueInput.fill(isoDateOffset(10));
    await form.submit();

    await expect(form.newClientFields).toBeVisible();
    await expect(form.fieldError("new_first_name")).toContainText("first name");
    await expect(form.fieldError("new_last_name")).toContainText("last name");
    await expect(form.newEmailInput).toHaveValue("new.client@example.invalid");
    await expect(form.itemInput).toHaveValue("Card holder");
    await expect(form.fieldError("item")).toHaveCount(0);
  });
});

test.describe("Order page — a refused save stays on the page (OR2a, OR12, OR13)", () => {
  test("a backwards due date is explained in place, the notes being written survive, and nothing is saved", async ({
    adminPage,
  }) => {
    await feature("Orders");
    await description(
      "OR13: the order page saves to its own URL precisely so a refused save can re-render right " +
        "here. The thing worth proving is the notes: a long note half-written when the date mistake " +
        "happens must come back in the box, and a fresh load afterwards must show nothing was stored."
    );
    const orderPage = new OrderPage(adminPage);

    await gotoPath(adminPage, "/orders");
    await adminPage.getByRole("link", { name: "Belt Set", exact: true }).click();
    await expect(adminPage).toHaveURL(/\/orders\/\d+/);
    const orderUrl = adminPage.url();
    const storedDue = await orderPage.dueInput.inputValue();
    const backwardsDue = shiftIso(await orderPage.startInput.inputValue(), -7);

    await orderPage.notesInput.fill("Client asked for a darker edge paint");
    await orderPage.dueInput.fill(backwardsDue);
    await orderPage.saveChanges();

    await expect(orderPage.fieldError("due")).toContainText("is before the start date");
    await expect(orderPage.dueInput).toHaveValue(backwardsDue);
    await expect(orderPage.notesInput).toHaveValue("Client asked for a darker edge paint");

    // Nothing was written.
    await gotoPath(adminPage, orderUrl);
    await expect(orderPage.dueInput).toHaveValue(storedDue);
    await expect(orderPage.notesInput).not.toHaveValue("Client asked for a darker edge paint");
  });

  test("a name that's only spaces is explained, and the order keeps the name it had", async ({ adminPage }) => {
    await feature("Orders");
    await description(
      "OR12: on an edit, an emptied Item used to be silently ignored — the save looked like it " +
        "worked and the old name quietly stayed. It's now a message, and the stored name is untouched."
    );
    const orderPage = new OrderPage(adminPage);

    await gotoPath(adminPage, "/orders");
    await adminPage.getByRole("link", { name: "Belt Set", exact: true }).click();

    await orderPage.itemInput.fill("    ");
    await orderPage.saveChanges();

    await expect(orderPage.fieldError("item")).toContainText("can't be blank or only spaces");
    await expect(orderPage.heading).toHaveText("Belt Set");
  });
});

test.describe("Timeline quick edit — a refused save reopens its dialog (OR2a, OR13)", () => {
  test("a backwards due date brings the dialog back open, explained, with the dates that were typed", async ({
    adminPage,
  }) => {
    await feature("Timeline");
    await description(
      "OR13: the timeline can't be re-rendered from the modal's POST URL, so a refused quick edit " +
        "redirects back to the same window and that order's dialog reopens by itself. Without the " +
        "reopen, the page would reload looking exactly as though the save had simply been lost."
    );
    const timeline = new TimelinePage(adminPage);
    await timeline.goto();

    const dialog = await timeline.openOrderModalByItem("Belt Set");
    const storedDue = await dialog.locator('input[name="due"]').inputValue();
    const backwardsDue = shiftIso(await dialog.locator('input[name="start"]').inputValue(), -3);
    await dialog.locator('input[name="due"]').fill(backwardsDue);
    await dialog.getByRole("button", { name: "Save" }).click();

    // The message only exists on the reloaded page, so asserting it first is
    // also what waits out the redirect.
    const reopened = await timeline.orderDialogByItem("Belt Set");
    await expect(timeline.dialogFieldError(reopened, "due")).toContainText("is before the start date");
    await expect(reopened).toBeVisible();
    await expect(reopened.locator('input[name="due"]')).toHaveValue(backwardsDue);

    // It closes like any other dialog...
    await reopened.locator("[data-close-modal]").click();
    await expect(reopened).toBeHidden();

    // ...nothing was saved, and the message is one-shot: it doesn't come back.
    await timeline.goto();
    const fresh = await timeline.orderDialogByItem("Belt Set");
    await expect(fresh).toBeHidden();
    await expect(timeline.dialogFieldError(fresh, "due")).toHaveCount(0);
    await expect(fresh.locator('input[name="due"]')).toHaveValue(storedDue);
  });
});

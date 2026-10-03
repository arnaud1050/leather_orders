import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { CalendarPage } from "../pages/CalendarPage";
import { calendarEventByKey, clientByKey, clientFullName } from "../fixtures/testData";

/**
 * CAL-14 / MOD8 — a refused event save reopens the dialog it came from,
 * with the message inside it and what was typed still filled in.
 *
 * pytest proves the HTML that comes back is right: the dialog carries
 * `data-open-on-load`, the message sits in its slot, the fields hold the
 * typed values. What only a browser can show is the rest:
 *
 * - the dialog actually opens (a script calls `showModal()`), over the grid;
 * - the dialog's own scripts run again on the reopened form — an "All day"
 *   tick still disables the time fields, and the invite hint and the
 *   "send invite" button follow the client that was picked;
 * - Cancel still closes it, and the next visit doesn't reopen it.
 *
 * Every save here is refused, and a refused save writes nothing and never
 * reaches Google — so these share the seeded event with every other spec
 * and with the other browser projects running at the same time.
 */

test.describe("Calendar — a refused event save reopens its dialog (CAL-14, MOD8)", () => {
  test("a new event that ends before it starts comes back open, with everything typed", async ({
    adminPage,
  }) => {
    await feature("Calendar");
    await description(
      "The dialog used to close on a refusal and put the error at the top of the page, with the form " +
        "blank. Now it reopens: the red message inside it, the title, times, notes and client as typed, " +
        "and the invite controls recomputed for that client by the dialog's own script."
    );
    const calendar = new CalendarPage(adminPage);
    const ada = clientByKey("ada");
    const dialog = calendar.newEventDialog;

    await calendar.gotoWeek();
    await calendar.openNewEvent();
    await expect(dialog).toBeVisible();
    await calendar.field(dialog, "title").fill("E2E refused consultation");
    await calendar.field(dialog, "start_time").fill("15:00");
    await calendar.field(dialog, "end_time").fill("14:00");
    await calendar.field(dialog, "description").fill("Bring the swatches");
    await calendar.field(dialog, "client_id").selectOption({ label: clientFullName(ada) });
    await dialog.getByRole("button", { name: "Add event", exact: true }).click();

    // The message only exists on the page that came back, so seeing it is
    // what proves the round trip happened — the dialog was open before too.
    await expect(calendar.notice(dialog)).toContainText("ends before it starts");
    await expect(dialog).toBeVisible();
    await expect(calendar.notice(dialog)).toHaveClass(/save-notice--error/);

    await expect(calendar.field(dialog, "title")).toHaveValue("E2E refused consultation");
    await expect(calendar.field(dialog, "start_time")).toHaveValue("15:00");
    await expect(calendar.field(dialog, "end_time")).toHaveValue("14:00");
    await expect(calendar.field(dialog, "description")).toHaveValue("Bring the swatches");
    await expect(calendar.field(dialog, "client_id").locator("option:checked")).toHaveText(
      clientFullName(ada)
    );

    // Recomputed by the script on load, from the reselected client.
    await expect(dialog.locator("[data-guest-hint]")).toContainText(ada.email);
    await expect(dialog.locator("[data-invite-button]")).toBeVisible();

    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();

    // The message was used up by that visit: the next one opens closed.
    await calendar.gotoWeek();
    await expect(dialog).toBeHidden();
    await expect(calendar.eventChip("E2E refused consultation")).toHaveCount(0);
  });

  test("a refused all-day event comes back with All day ticked and the times disabled", async ({
    adminPage,
  }) => {
    await feature("Calendar");
    await description(
      "The all-day toggle disables the time fields from script. A reopened dialog has to run that " +
        "again from the restored tick, or it shows live time fields for an event that has none."
    );
    const calendar = new CalendarPage(adminPage);
    const dialog = calendar.newEventDialog;

    await calendar.gotoWeek();
    await calendar.openNewEvent();
    await calendar.field(dialog, "title").fill("E2E refused all-day");
    await calendar.field(dialog, "all_day").check();
    await expect(calendar.field(dialog, "start_time")).toBeDisabled();
    const start = await calendar.field(dialog, "start_date").inputValue();
    const dayBefore = new Date(`${start}T00:00:00Z`);
    dayBefore.setUTCDate(dayBefore.getUTCDate() - 1);
    await calendar.field(dialog, "end_date").fill(dayBefore.toISOString().slice(0, 10));
    await dialog.getByRole("button", { name: "Add event", exact: true }).click();

    await expect(calendar.notice(dialog)).toContainText("ends before it starts");
    await expect(dialog).toBeVisible();
    await expect(calendar.field(dialog, "all_day")).toBeChecked();
    await expect(calendar.field(dialog, "start_time")).toBeDisabled();
    await expect(calendar.field(dialog, "end_time")).toBeDisabled();
    await expect(calendar.field(dialog, "title")).toHaveValue("E2E refused all-day");
  });

  test("a refused edit reopens that event's dialog, and the event is untouched", async ({
    adminPage,
  }) => {
    await feature("Calendar");
    await description(
      "Each event on screen has its own dialog. A refused edit must reopen that one — not the new-event " +
        "dialog, not none — with the typed title, and leave the stored event exactly as it was."
    );
    const calendar = new CalendarPage(adminPage);
    const seeded = calendarEventByKey("fitting");

    await calendar.gotoWeek();
    const dialog = await calendar.openEvent(seeded.title);
    await expect(dialog).toBeVisible();
    await calendar.field(dialog, "title").fill(`${seeded.title} (moved)`);
    await calendar.field(dialog, "end_time").fill("08:00");
    await dialog.getByRole("button", { name: "Save", exact: true }).click();

    await expect(calendar.notice(dialog)).toContainText("ends before it starts");
    await expect(dialog).toBeVisible();
    await expect(calendar.newEventDialog).toBeHidden();
    await expect(calendar.field(dialog, "title")).toHaveValue(`${seeded.title} (moved)`);
    await expect(calendar.field(dialog, "end_time")).toHaveValue("08:00");

    await dialog.getByRole("button", { name: "Cancel" }).click();
    await calendar.gotoWeek();
    await expect(calendar.eventChip(seeded.title)).toHaveCount(1);
    await expect(calendar.eventChip(`${seeded.title} (moved)`)).toHaveCount(0);
  });
});

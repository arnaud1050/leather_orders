import { Locator, Page } from "@playwright/test";
import { step } from "allure-js-commons";
import { BasePage } from "./BasePage";
import { gotoPath } from "../lib/nav";

/**
 * The calendar's week view (`/week`, `templates/calendar_week.html`) and
 * its event dialogs (`templates/_event_dialogs.html`): one pre-rendered
 * `<dialog>` for a new event, and one per event on screen.
 *
 * The dialogs only render when a connected account granted calendar access
 * (CAL-4) — the seed provides a paused one (seed/e2e-data.json `calendar`).
 * Nothing here may save an event successfully: that goes out to Google.
 * A refused save is rejected before any provider call, which is what makes
 * the refusal path testable at all.
 */
export class CalendarPage extends BasePage {
  readonly newEventDialog: Locator;

  constructor(page: Page) {
    super(page);
    this.newEventDialog = page.locator("#event-modal-new");
  }

  async gotoWeek(): Promise<void> {
    await step("Go to the calendar's week view", async () => {
      await gotoPath(this.page, "/week");
    });
  }

  async openNewEvent(): Promise<void> {
    await step("Open the New event dialog", async () => {
      await this.page.getByRole("button", { name: "+ New event" }).click();
    });
  }

  /** The chip for an event on the grid, by its title. */
  eventChip(title: string): Locator {
    return this.page.locator(".chip--event").filter({ hasText: title });
  }

  /** The edit dialog a chip opens, found through the chip's own
   * `data-open-modal` rather than by guessing the event's id. */
  async eventDialog(title: string): Promise<Locator> {
    const id = await this.eventChip(title).first().getAttribute("data-open-modal");
    if (!id) throw new Error(`No chip for an event titled "${title}"`);
    return this.page.locator(`#${id}`);
  }

  async openEvent(title: string): Promise<Locator> {
    return await step(`Open the event "${title}"`, async () => {
      const dialog = await this.eventDialog(title);
      await this.eventChip(title).first().click();
      return dialog;
    });
  }

  field(dialog: Locator, name: string): Locator {
    return dialog.locator(`[name="${name}"]`);
  }

  /** The save's message inside a dialog (REQUIREMENTS MOD8). */
  notice(dialog: Locator): Locator {
    return dialog.locator(".save-notice");
  }
}

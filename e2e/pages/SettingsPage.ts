import { Locator, Page, expect } from "@playwright/test";
import { step } from "allure-js-commons";
import { BasePage } from "./BasePage";
import { gotoPath } from "../lib/nav";

/**
 * The Settings categories that own a drag-reorderable list (CL8, LST10) —
 * `templates/settings.html`, sections 'orders' and 'clients'.
 *
 * Every one of those lists is the same native HTML5 pattern: `draggable`
 * `<li>`s, a grip handle, a `dragover` handler that reorders the DOM by
 * midpoint, and a `dragend` handler that POSTs the resulting order as JSON.
 * Nothing saves until `dragend` fires, and there is no Save button — which
 * is exactly why this needs a real browser to test.
 */
export class SettingsPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }

  async gotoClients(): Promise<void> {
    await step("Go to Settings > Clients", async () => {
      await gotoPath(this.page, "/settings/clients");
    });
  }

  async gotoOrders(): Promise<void> {
    await step("Go to Settings > Orders", async () => {
      await gotoPath(this.page, "/settings/orders");
    });
  }

  sourceOptionItems(): Locator {
    return this.page.locator("#source-option-list .settings-source-list__item");
  }

  orderColumnItems(): Locator {
    return this.page.locator("#order-column-list .settings-source-list__item");
  }

  /** The labels in the order they currently render, trimmed of the
   * Hide/Delete button text each row carries alongside them. */
  async sourceOptionLabels(): Promise<string[]> {
    return await step("Read the source-option order", async () => {
      return this.sourceOptionItems()
        .locator(".settings-source-list__label")
        .allInnerTexts();
    });
  }

  async orderColumnKeys(): Promise<string[]> {
    return await step("Read the Orders-list column order", async () => {
      return this.orderColumnItems().evaluateAll((items) =>
        items.map((el) => (el as HTMLElement).dataset.key ?? "")
      );
    });
  }

  /**
   * Drag `source` onto `target` using real HTML5 drag events.
   *
   * Playwright's own `dragTo()` drives mouse events, which is enough for
   * pointer-based sortables but does not reliably produce the
   * `dragstart`/`dragover`/`dragend` sequence these lists actually listen
   * for. So the events are dispatched directly, sharing one DataTransfer
   * across all three the way a real drag does — the handlers read
   * `dataTransfer.setData` on start and `clientY` on over, and fire the
   * save on end.
   */
  async dragItemOnto(source: Locator, target: Locator, position: "above" | "below" = "above") {
    await step(`Drag an item ${position} another`, async () => {
      const sourceHandle = await source.elementHandle();
      const targetHandle = await target.elementHandle();
      if (!sourceHandle || !targetHandle) throw new Error("drag source/target not found");

      // Start listening *before* the drag: dragend fires the save
      // synchronously, so a waitForResponse registered afterwards can miss
      // a response that already arrived — the classic way this kind of
      // test goes green locally and flaky in CI.
      const saved = this.page.waitForResponse(
        (r) => r.url().includes("/reorder") && r.request().method() === "POST"
      );

      await this.page.evaluate(
        ([from, to, pos]) => {
          const dataTransfer = new DataTransfer();
          const rect = (to as HTMLElement).getBoundingClientRect();
          // The dragover handler splits on the target's vertical midpoint:
          // above it inserts before, below inserts after.
          const clientY = pos === "above" ? rect.top + 2 : rect.bottom - 2;

          from.dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer }));
          to.dispatchEvent(
            new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer, clientY })
          );
          to.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer }));
          from.dispatchEvent(new DragEvent("dragend", { bubbles: true, dataTransfer }));
        },
        [sourceHandle, targetHandle, position] as const
      );

      const response = await saved;
      expect(response.status(), "the reorder POST should have been accepted").toBeLessThan(400);
    });
  }

  /** The save has no UI feedback at all, so the only honest confirmation
   * that it stuck is asking the server for the page again. */
  async reload(): Promise<void> {
    await step("Reload to confirm the new order came back from the server", async () => {
      await this.page.reload({ waitUntil: "domcontentloaded" });
    });
  }

  columnToggle(key: string): Locator {
    return this.page
      .locator(`#order-column-list .settings-source-list__item[data-key="${key}"]`)
      .locator("button");
  }

  async toggleColumn(key: string): Promise<void> {
    await step(`Toggle the '${key}' column's visibility`, async () => {
      await this.columnToggle(key).click();
    });
  }
}

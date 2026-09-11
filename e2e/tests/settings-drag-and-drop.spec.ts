import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { SettingsPage } from "../pages/SettingsPage";
import { OrdersListPage } from "../pages/OrdersListPage";

/**
 * The drag-reorderable lists (CL8, LST10) are the one interaction in this
 * app with no coverage at any level: pytest covers the reorder *endpoints*
 * thoroughly, but nothing covers the drag that calls them — and between the
 * two sits a `dragend` handler that reads the DOM and builds the payload. A
 * wrong selector or a reversed sort there saves an order nobody asked for,
 * with no Save button and no confirmation to notice it by.
 *
 * These tests mutate per-company settings and put them back afterwards.
 * They share one server and one database with every other spec, so keep
 * this file's subject matter to itself: nothing else asserts on
 * source-option order or the Orders-list column layout.
 */

/** ORDER_COLUMNS in app.py — key to the header label it renders. */
const COLUMN_LABELS: Record<string, string> = {
  item: "Item",
  client: "Client",
  type: "Type",
  status: "Status",
  start: "Start",
  due: "Due",
  total: "Total",
  paid: "Paid",
  balance: "Balance",
};

test.describe("Settings > Clients — source option reorder (CL8)", () => {
  test("dragging an option to the top persists after a reload", async ({ adminPage }) => {
    await feature("Settings");
    await description(
      "CL8: source options are reordered by drag-and-drop, saved by a JSON fetch fired on dragend with " +
        "no Save button. The reorder is only real if it survives a reload — that's what proves the " +
        "payload the browser built actually reached /settings/sources/reorder and was stored, rather " +
        "than the row merely having moved in the DOM."
    );
    const settings = new SettingsPage(adminPage);
    await settings.gotoClients();

    const before = await settings.sourceOptionLabels();
    expect(before.length).toBeGreaterThan(2);

    // Drag the last option above the first.
    const items = settings.sourceOptionItems();
    await settings.dragItemOnto(items.last(), items.first(), "above");

    try {
      const afterDrag = await settings.sourceOptionLabels();
      expect(afterDrag[0]).toBe(before[before.length - 1]);

      await settings.reload();

      expect(await settings.sourceOptionLabels()).toEqual(afterDrag);
    } finally {
      // Put the original order back even on failure, so nothing else in the
      // suite inherits a reshuffled list.
      const restored = settings.sourceOptionItems();
      await settings.dragItemOnto(restored.first(), restored.last(), "below");
    }

    await settings.reload();
    expect(await settings.sourceOptionLabels()).toEqual(before);
  });
});

test.describe("Settings > Orders — Orders-list column layout (LST9, LST10)", () => {
  test("dragging a column changes the order /orders renders its headers in", async ({ adminPage }) => {
    await feature("Settings");
    await description(
      "LST10: the column editor saves the instant a row is dropped. The assertion that matters isn't " +
        "the settings page redrawing itself — it's /orders actually rendering its headers in the new " +
        "order afterwards, which is the whole point of the preference."
    );
    const settings = new SettingsPage(adminPage);
    const orders = new OrdersListPage(adminPage);
    await settings.gotoOrders();

    const before = await settings.orderColumnKeys();
    expect(before.length).toBeGreaterThan(2);
    const movedKey = before[before.length - 1];

    const items = settings.orderColumnItems();
    await settings.dragItemOnto(items.last(), items.first(), "above");

    try {
      await settings.reload();

      expect(await settings.orderColumnKeys()).toEqual([
        movedKey,
        ...before.slice(0, before.length - 1),
      ]);

      // The preference is only worth anything if the list itself obeys it.
      await orders.goto();
      const headers = await orders.table.locator("thead a.sort-link").allInnerTexts();
      // Compared case-insensitively: the header is uppercased by CSS, so
      // innerText reads "BALANCE" while the label really is "Balance".
      // The casing is presentation, not part of the column's identity.
      expect(headers[0].trim().toLowerCase()).toBe(COLUMN_LABELS[movedKey].toLowerCase());
    } finally {
      // Restore even if an assertion above failed — this is company-wide
      // saved state, and leaving it reshuffled would hand the next test a
      // layout it never asked for.
      await settings.gotoOrders();
      const restored = settings.orderColumnItems();
      await settings.dragItemOnto(restored.first(), restored.last(), "below");
    }

    await settings.reload();
    expect(await settings.orderColumnKeys()).toEqual(before);
  });

  test("hiding a column removes it from /orders, and showing it puts it back", async ({ adminPage }) => {
    await feature("Settings");
    await description(
      "LST10/LST9: visibility is a plain POST-and-redirect, not part of the drag. Hiding the Total " +
        "column must drop that header from /orders entirely while leaving the rows intact — the data " +
        "is kept, it just stops rendering."
    );
    const settings = new SettingsPage(adminPage);
    const orders = new OrdersListPage(adminPage);

    await orders.goto();
    await expect(orders.table.locator("thead")).toContainText("Total");

    await settings.gotoOrders();
    await settings.toggleColumn("total");

    try {
      await orders.goto();
      await expect(orders.table.locator("thead")).not.toContainText("Total");
      // The rows are still there — this hid a column, not the orders.
      await expect(orders.rowByItem("Custom Tote")).toBeVisible();
    } finally {
      await settings.gotoOrders();
      await settings.toggleColumn("total");
    }

    await orders.goto();
    await expect(orders.table.locator("thead")).toContainText("Total");
  });
});

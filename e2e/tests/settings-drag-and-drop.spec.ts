import { test, expect } from "../fixtures/auth.fixture";

/**
 * SKELETON — not yet backed by real selectors.
 *
 * Every drag-reorderable list in the app (source options, order types,
 * inventory units, the Orders-list column editor, the Analytics layout)
 * shares one native HTML5 drag-and-drop pattern — draggable="true" list
 * items, a grip handle, a JSON fetch() fired on `dragend` — but each lives
 * on a different settings page whose markup this scaffold hasn't read yet.
 * This is real, first-priority test surface (see the QA plan: it's the
 * only interaction in the whole app with zero pytest coverage anywhere),
 * so it's stubbed out here rather than skipped silently.
 *
 * To fill these in:
 *   1. Read the relevant template (templates/settings.html for source
 *      options/order types, inventory/templates/_settings_units.html and
 *      _settings_columns.html, analytics.html) to get real selectors for
 *      the draggable items and their grip handles.
 *   2. Playwright has no built-in "drag this HTML5 draggable item" action
 *      — `locator.dragTo()` works for simple cases, but native
 *      draggable="true" drag-and-drop usually needs manual dispatch of
 *      dragstart/dragover/drop DOM events (see Playwright's own docs on
 *      "Drag and Drop" for the dataTransfer polyfill pattern) since a raw
 *      mouse-move sequence doesn't fire the HTML5 drag events these pages
 *      listen for.
 *   3. Assert the persisted order survives a reload (these save instantly
 *      on `dragend`, no Save button) by re-reading the list from the page,
 *      not from a mocked network response — the fetch's response body
 *      confirms the request happened, not that the row order it produced
 *      is actually correct.
 */
test.describe("Settings > Clients — source option reorder (CL8)", () => {
  test.fixme("dragging a source option to a new position persists after reload", async ({ adminPage }) => {
    await adminPage.goto("/settings/clients");
    // TODO: locate draggable items, e.g. page.locator('.settings-source-list__item')
    // TODO: perform the drag (see class-level note above on HTML5 DnD)
    // TODO: reload and assert the new order
  });

  test.fixme("dragging an id belonging to another company is silently ignored, not an error", async () => {
    // TODO: this one may be better suited to an API-level test (POST
    // /settings/sources/reorder directly with a foreign id) than a UI
    // drag — the UI never renders another company's row to drag in the
    // first place, so the interesting case is what the endpoint does with
    // a tampered payload, not what a mouse can do.
  });
});

test.describe("Orders list — column reorder and hide/show (LST6-LST12)", () => {
  test.fixme("dragging a column row changes /orders' rendered column order", async ({ adminPage }) => {
    await adminPage.goto("/settings/orders");
    // TODO
  });

  test.fixme("hiding the Type column hides it on /orders even though order types exist", async ({ adminPage }) => {
    await adminPage.goto("/settings/orders");
    // TODO: click the Type row's Hide button (not a drag — LST10's
    // reorder and toggle are two different actions)
  });
});

test.describe("Analytics — section/card layout reorder (AN10, AN11)", () => {
  test.fixme("dragging a card within its own section persists after reload", async ({ adminPage }) => {
    await adminPage.goto("/analytics");
    // TODO
  });

  test.fixme("a card cannot be dragged into a different section", async ({ adminPage }) => {
    await adminPage.goto("/analytics");
    // TODO: this is the one negative case worth its own test — AN11 says
    // the server re-validates this even if a tampered client payload tried
    // to move a card across sections, so consider an API-level check here
    // too, not only a UI drag that the frontend already prevents.
  });
});

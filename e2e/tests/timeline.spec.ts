import { test, expect } from "../fixtures/auth.fixture";
import { feature, description, step } from "allure-js-commons";
import { TimelinePage } from "../pages/TimelinePage";
import { clientByKey, clientFullName, formatMoney, orderByKey } from "../fixtures/testData";

// Every test here starts a fresh browser context via `adminPage` (see
// fixtures/auth.fixture.ts), so localStorage — which is where the status
// filter and sort choice actually live (TL7) — always starts empty. Don't
// reuse a page across these tests; that's exactly how one test's filter
// leaks into the next one's expectations.
test.describe("Timeline — default view (TL1-TL6)", () => {
  test("shows a row per in-window order, hides cancelled and far-past orders", async ({ adminPage }) => {
    await feature("Timeline");
    await description(
      "TL1/OR1g: the timeline shows only orders overlapping its 8-week window. A cancelled order never " +
        "reaches this query at all, and an order delivered weeks before the window opened simply falls " +
        "outside it — both still exist and still show on /orders, just not here."
    );
    const timeline = new TimelinePage(adminPage);
    await timeline.goto();

    // Seeded: Ada (confirmed+ready order), Grace (confirmed, rush),
    // Margaret (ready), Radia (confirmed) = 4 in-window orders.
    // Katherine's order is cancelled (OR1g: never reaches the timeline
    // query) and Ada's second order is delivered ~30-40 days ago, well
    // outside the 8-week forward window — both must be absent here even
    // though both are real orders you'd find on /orders.
    await expect(timeline.orderCount).toHaveText("4");
    await expect(timeline.rowByOrderItem("Custom Tote")).toBeVisible();
    await expect(timeline.rowByOrderItem("Rush Duffel")).toBeVisible();
    await expect(timeline.rowByOrderItem("Belt Set")).toBeVisible();
    await expect(timeline.rowByOrderItem("Laptop Sleeve")).toBeVisible();
    await expect(timeline.rowByOrderItem("Cancelled Satchel")).toHaveCount(0);
    await expect(timeline.rowByOrderItem("Wallet Repair")).toHaveCount(0);
  });

  test("a rush order's bar is styled red and never carries a filterable status of its own", async ({
    adminPage,
  }) => {
    await feature("Timeline");
    await description(
      "OR1d-ii: rush is a boolean flag, not a lifecycle stage. Its bar renders in the red 'rush' colour " +
        "regardless of the order's actual status, and there is deliberately no 'rush' entry in the status " +
        "legend — rush is surfaced via sort ('...rush first'), never via a filter."
    );
    const timeline = new TimelinePage(adminPage);
    await timeline.goto();
    const rushRow = timeline.rowByOrderItem("Rush Duffel");
    await expect(rushRow.locator(".timeline__bar")).toHaveClass(/timeline__bar--rush/);
    // OR1d-ii: rush is a flag, not a stage — there's no "rush" legend button.
    await expect(adminPage.locator('#status-legend .legend__item[data-status="rush"]')).toHaveCount(0);
  });

  test("only the returning client (2+ orders) shows the star", async ({ adminPage }) => {
    await feature("Timeline");
    await description(
      "TL6/CL2: Client.is_returning is derived from having 2+ orders. Ada (two seeded orders) gets the " +
        "star next to her name; Grace (one order) does not."
    );
    const timeline = new TimelinePage(adminPage);
    await timeline.goto();
    const ada = clientFullName(clientByKey("ada"));
    const grace = clientFullName(clientByKey("grace"));

    await expect(
      timeline.rowByOrderItem("Custom Tote").locator(".timeline__label", { hasText: ada }).locator(".timeline__star")
    ).toBeVisible();
    await expect(
      timeline.rowByOrderItem("Rush Duffel").locator(".timeline__label", { hasText: grace }).locator(".timeline__star")
    ).toHaveCount(0);
  });
});

test.describe("Timeline — status filter persists via localStorage (TL7, TL8, TL8a)", () => {
  test("hiding 'In progress' hides every order at the confirmed stage, dims the button, and updates the count", async ({
    adminPage,
  }) => {
    await feature("Timeline");
    await description(
      "TL8a: 'Confirmed' and 'In progress' are the same stored status shown under two labels (OR1c), and " +
        "share one legend button — hiding it must hide every order at that stored status regardless of " +
        "which label it currently reads as, and recompute the visible order count (TL8)."
    );
    const timeline = new TimelinePage(adminPage);
    await timeline.goto();

    // TL8a: Confirmed and "In progress" are the same stored status and
    // share one button — Ada's Custom Tote, Grace's Rush Duffel and
    // Radia's order are all `status=confirmed` (two of them already read
    // as "In progress" because their start date has passed — OR1c), so
    // one click hides all three, leaving only Margaret's ready order.
    await timeline.toggleStatusFilter("confirmed");
    await timeline.expectStatusFilteredOff("confirmed");
    await expect(timeline.orderCount).toHaveText("1");
    await expect(timeline.rowByOrderItem("Belt Set")).toBeVisible();
    await expect(timeline.rowByOrderItem("Custom Tote")).toBeHidden();
    await expect(timeline.rowByOrderItem("Rush Duffel")).toBeHidden();
    await expect(timeline.rowByOrderItem("Laptop Sleeve")).toBeHidden();
  });

  test("the filter survives navigating to the next window and back", async ({ adminPage }) => {
    await feature("Timeline");
    await description(
      "TL7: the status filter is persisted in localStorage rather than a URL query param specifically so " +
        "it carries over automatically across prev/next window navigation, each of which is a real page load."
    );
    const timeline = new TimelinePage(adminPage);
    await timeline.goto();
    await timeline.toggleStatusFilter("ready");
    await timeline.expectStatusFilteredOff("ready");

    await timeline.goNextWindow();
    await timeline.expectStatusFilteredOff("ready");

    await timeline.goPrevWindow();
    await timeline.expectStatusFilteredOff("ready");
  });
});

test.describe("Timeline — sort (TL9, TL9a)", () => {
  test("'due date, rush first' floats the only rush order to the top", async ({ adminPage }) => {
    await feature("Timeline");
    await description(
      "TL9a: the '...rush first' sorts group by is_rush before sorting by the chosen date within each " +
        "group, so rush work always floats to the top regardless of its own due date."
    );
    const timeline = new TimelinePage(adminPage);
    await timeline.goto();
    await timeline.setSort("due_rush");

    const firstRowLabel = timeline.rows().first().locator(".timeline__label-name");
    await expect(firstRowLabel).toHaveText(clientFullName(clientByKey("grace")));
  });

  test("the choice is remembered on reload", async ({ adminPage }) => {
    await feature("Timeline");
    await description("TL9: the sort choice is persisted in localStorage and re-applied on every page load.");
    const timeline = new TimelinePage(adminPage);
    await timeline.goto();
    await timeline.setSort("value");
    await adminPage.reload();
    await expect(timeline.sortSelect).toHaveValue("value");
  });
});

test.describe("Timeline — modals (MOD1, MOD2, TL11, OR1a, OR1d-i)", () => {
  test("the client modal opens pre-filled and saves without leaving the timeline", async ({ adminPage }) => {
    await feature("Timeline");
    await description(
      "MOD1/MOD2/MOD5: clicking a client name opens a pre-rendered <dialog> pre-filled with that client's " +
        "details; saving posts back to edit_client() and, via return_to, redirects back to the same " +
        "timeline window rather than resetting to a default view."
    );
    const timeline = new TimelinePage(adminPage);
    await timeline.goto();
    const ada = clientByKey("ada");

    const dialog = await timeline.openClientModal(await firstClientIdFor(adminPage, "Ada Lovelace"));
    await expect(dialog.locator('input[name="first_name"]')).toHaveValue(ada.firstName);
    await expect(dialog.locator('input[name="email"]')).toHaveValue(ada.email);

    await dialog.locator('input[name="phone"]').fill("555-9999");
    await dialog.getByRole("button", { name: "Save" }).click();

    // return_to carries the exact timeline URL, so saving lands back here,
    // not on a default view (MOD5). toHaveURL matches a RegExp against the
    // full absolute URL (scheme and host included), not just the path —
    // hence no `^` anchor here, only `$`.
    await expect(adminPage).toHaveURL(/\/(timeline\/\d{4}\/\d{1,2}\/\d{1,2})?$/);
  });

  test("the order modal shows a read-only total and only offers legal forward transitions", async ({
    adminPage,
  }) => {
    await feature("Timeline");
    await description(
      "TL11/OR1a/OR1d-i: the quick-edit order modal's Total is read-only (no room for a line editor in a " +
        "dialog), its status dropdown offers only settable_statuses(current) — forward transitions minus " +
        "cancelled — and its rush checkbox appears only when can_rush (status='confirmed') is true."
    );
    const timeline = new TimelinePage(adminPage);
    await timeline.goto();
    const { order } = orderByKey("grace-rush-order");

    const dialog = await timeline.openOrderModalByItem("Rush Duffel");
    await expect(dialog.locator(".modal__readout")).toHaveText(formatMoney(order.price));

    // OR1a: settable_statuses('confirmed') is exactly ['confirmed', 'ready']
    // — cancelled is a legal transition too, but is deliberately excluded
    // here because cancelling needs a reason, collected on the full page.
    const statusValues = await dialog.locator('select[name="status"] option').evaluateAll((opts) =>
      opts.map((o) => (o as HTMLOptionElement).value)
    );
    expect(statusValues).toEqual(["confirmed", "ready"]);

    // OR1d-i: can_rush is true only at 'confirmed', so the checkbox exists
    // here and reflects the seeded is_rush=true.
    await expect(dialog.locator('input[name="is_rush"]')).toBeChecked();
  });
});

/** The modal ids are keyed by client id, which the fixture doesn't hardcode
 * (create_company/Client autoincrement, not something a seed script should
 * assume) — so look it up off the rendered page instead of guessing it. */
async function firstClientIdFor(page: import("@playwright/test").Page, clientName: string): Promise<string> {
  return await step(`Look up the timeline client id for "${clientName}"`, async () => {
    const button = page.locator(`[data-open-client]:has-text("${clientName}")`).first();
    const id = await button.getAttribute("data-open-client");
    if (!id) throw new Error(`Could not find a timeline client button for "${clientName}"`);
    return id;
  });
}

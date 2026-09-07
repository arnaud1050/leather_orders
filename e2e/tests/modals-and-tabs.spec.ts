import { test, expect } from "../fixtures/auth.fixture";
import { clientByKey, clientFullName } from "../fixtures/testData";

/**
 * SKELETON — the timeline's own modals are already covered concretely in
 * timeline.spec.ts (their markup was read this session). What's stubbed
 * here is everything MOD1-MOD6 in REQUIREMENTS.md describes beyond that:
 * the full client/order pages' own tab navigation, and `return_to`
 * surviving a tab switch — templates/client_page.html and
 * templates/order_page.html haven't been read yet, so these are TODOs
 * rather than guesses at selectors.
 */
test.describe("Client page — Information / Orders tabs (MOD3, CL15)", () => {
  test.fixme("switching tabs carries return_to through, and Orders renders a sortable table", async ({
    adminPage,
  }) => {
    const ada = clientFullName(clientByKey("ada"));
    await adminPage.goto("/orders?return_to=%2Forders");
    await adminPage.getByRole("link", { name: ada }).first().click();
    // TODO: read templates/client_page.html for the tab links' real
    // selectors (likely `.settings-nav` per docs/views.md) and assert:
    //   1. the "Orders" tab link, when clicked, carries return_to=/orders
    //      through to /clients/<id>/orders?return_to=...
    //   2. that tab renders both of Ada's orders (Custom Tote, Wallet
    //      Repair) in the fixed CLIENT_ORDER_SORT_KEYS columns (CL15)
    //   3. clicking back to "Information" still carries return_to
  });
});

test.describe("Order page — Details / Materials / Billing tabs (MOD4)", () => {
  test.fixme("all three tabs render and carry return_to through", async ({ adminPage }) => {
    await adminPage.goto("/orders?return_to=%2Forders");
    await adminPage.getByRole("link", { name: "Custom Tote" }).click();
    // TODO: read templates/order_page.html for the real tab selectors and
    // assert Details -> Materials -> Billing each keep return_to, and that
    // the bare /orders/<id> URL (no /details suffix) still renders the
    // Details tab (MOD4's "kept there for backward-link compatibility").
  });

  test.fixme("a form that omits a field leaves it untouched, not cleared (hard rule 9, OR3, OT6)", async () => {
    // TODO: this is the one with real bug-catching value — e.g. saving
    // from the timeline's quick-edit order modal (which has no
    // pickup_date field) must not clear a pickup_date already set on the
    // full order page. Needs the full order page's field selectors first.
  });
});

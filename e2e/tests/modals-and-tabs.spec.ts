import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { ClientPage } from "../pages/ClientPage";
import { OrderPage } from "../pages/OrderPage";
import { TimelinePage } from "../pages/TimelinePage";
import { clientByKey, clientFullName, isoDateOffset } from "../fixtures/testData";
import { gotoPath } from "../lib/nav";

/**
 * MOD3/MOD4 are marked "— gap —" in REQUIREMENTS.md's coverage map: tab
 * markup and `return_to` surviving a tab switch are things a route test
 * can't see, because each tab is a separate request and what matters is
 * what the *link* carried.
 */

/** The fixture doesn't hardcode row ids (they're autoincrement), so reach a
 * detail page the way a person would — by clicking the row's own link. */
async function openOrderByItem(page: import("@playwright/test").Page, item: string) {
  await gotoPath(page, "/orders");
  await page.getByRole("link", { name: item, exact: true }).click();
  await expect(page).toHaveURL(/\/orders\/\d+/);
}

test.describe("Client page — Information / Orders tabs (MOD3, CL15)", () => {
  test("both tabs render and carry return_to through the switch", async ({ adminPage }) => {
    await feature("Clients");
    await description(
      "MOD3/MOD5: the client page's tabs are real routes sharing one template, and each tab link " +
        "carries return_to so switching tabs doesn't lose the timeline window (or list) you arrived from."
    );
    const clientPage = new ClientPage(adminPage);
    const ada = clientFullName(clientByKey("ada"));

    await gotoPath(adminPage, "/clients");
    await adminPage.getByRole("link", { name: ada, exact: true }).click();
    await expect(adminPage).toHaveURL(/\/clients\/\d+/);
    await clientPage.expectActiveTab("Information");

    // The return_to that /clients handed over must still be on the Orders
    // tab's own link — that's the bit a route test can't check.
    await expect(clientPage.tab("Orders")).toHaveAttribute("href", /return_to=/);
    await clientPage.openTab("Orders");
    await expect(adminPage).toHaveURL(/\/clients\/\d+\/orders\?return_to=/);
    await clientPage.expectActiveTab("Orders");

    // ...and back again, without dropping it.
    await expect(clientPage.tab("Information")).toHaveAttribute("href", /return_to=/);
    await clientPage.openTab("Information");
    await expect(adminPage).toHaveURL(/\/clients\/\d+\?return_to=/);
  });

  test("the Orders tab lists that client's orders with the Invoice column /orders doesn't have", async ({
    adminPage,
  }) => {
    await feature("Clients");
    await description(
      "CL15: the Orders tab is a sortable table over one client's own roster, with a fixed column set " +
        "(Item/Status/Start/Due/Total/Paid/Balance/Invoice). Invoice is the column /orders lacks, and an " +
        "uninvoiced order renders an em dash there rather than a blank."
    );
    const clientPage = new ClientPage(adminPage);
    const ada = clientByKey("ada");

    await gotoPath(adminPage, "/clients");
    await adminPage.getByRole("link", { name: clientFullName(ada), exact: true }).click();
    await clientPage.openTab("Orders");

    for (const header of ["Item", "Status", "Start", "Due", "Total", "Paid", "Balance", "Invoice"]) {
      await expect(clientPage.ordersTable.locator("thead")).toContainText(header);
    }
    // Both of Ada's seeded orders, and only hers.
    await expect(clientPage.ordersTable.locator("tbody tr")).toHaveCount(ada.orders.length);
    await expect(clientPage.ordersTable).toContainText("Custom Tote");
    await expect(clientPage.ordersTable).toContainText("Wallet Repair");
    // Neither is invoiced by the fixture, so the Invoice cell is an em dash.
    await expect(clientPage.ordersTable.locator("tbody")).toContainText("—");
  });
});

test.describe("Order page — Details / Materials / Billing tabs (MOD4)", () => {
  test("the bare /orders/<id> URL lands on Details, and all three tabs carry return_to", async ({
    adminPage,
  }) => {
    await feature("Orders");
    await description(
      "MOD4: three tabs over one template, ordered Details -> Materials -> Billing. The bare " +
        "/orders/<id> URL deliberately stays on Details so older links keep working, and every tab " +
        "link carries return_to through (MOD5)."
    );
    const orderPage = new OrderPage(adminPage);
    await openOrderByItem(adminPage, "Custom Tote");

    await orderPage.expectActiveTab("Details");

    for (const tab of ["Details", "Materials", "Billing"] as const) {
      await expect(orderPage.tab(tab)).toHaveAttribute("href", /return_to=/);
    }

    await orderPage.openTab("Materials");
    await expect(adminPage).toHaveURL(/\/orders\/\d+\/materials\?return_to=/);
    await orderPage.expectActiveTab("Materials");

    await orderPage.openTab("Billing");
    await expect(adminPage).toHaveURL(/\/orders\/\d+\/billing\?return_to=/);
    await orderPage.expectActiveTab("Billing");
  });

  test("saving from the timeline modal leaves a pickup date the modal never rendered", async ({
    adminPage,
  }) => {
    await feature("Orders");
    await description(
      "Hard rule 9 / OR3 — the highest-value regression guard on this page. `pickup_date` is editable " +
        "only on the full order page; the timeline's quick-edit modal omits it entirely. An unguarded " +
        "write in edit_order() would therefore blank it on every save from that modal, and nothing on " +
        "screen would say so. Route tests cover the guard directly; this covers the two real surfaces " +
        "actually posting to the same endpoint."
    );
    const orderPage = new OrderPage(adminPage);
    const pickup = isoDateOffset(12);

    await openOrderByItem(adminPage, "Custom Tote");
    const orderUrl = adminPage.url();
    await orderPage.setPickupDate(pickup);

    await gotoPath(adminPage, orderUrl);
    await expect(orderPage.pickupDateInput).toHaveValue(pickup);

    // Now edit the same order from the timeline modal, which renders no
    // pickup_date field at all, and save.
    const timeline = new TimelinePage(adminPage);
    await timeline.goto();
    const dialog = await timeline.openOrderModalByItem("Custom Tote");
    await expect(dialog.locator('input[name="pickup_date"]')).toHaveCount(0);
    await dialog.locator('input[name="item"]').fill("Custom Tote");
    await dialog.getByRole("button", { name: "Save" }).click();

    await gotoPath(adminPage, orderUrl);
    await expect(orderPage.pickupDateInput).toHaveValue(pickup);
  });
});

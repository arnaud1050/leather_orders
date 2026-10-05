import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { createClientAndOrder, journeyTag } from "../lib/invoicing";
import { InvoicePage } from "../pages/InvoicePage";
import { OrderPage } from "../pages/OrderPage";
import { gotoPath } from "../lib/nav";

async function setClientProvince(page: import("@playwright/test").Page, clientId: number, province: string) {
  await gotoPath(page, `/clients/${clientId}`);
  await page.locator('select[name="province"]').selectOption(province);
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.waitForURL((url) => url.pathname !== `/clients/${clientId}`, { waitUntil: "domcontentloaded" });
}

test.describe("Invoices: correcting one already sent (F15)", () => {
  test("sent without tax, set back to draft once the province is known, then sent again with it, same number", async ({
    invoicingPage: page,
  }, testInfo) => {
    await feature("Invoicing");
    await description(
      "F15: the way to correct a sent invoice is to set it back to Draft, which makes it live again, " +
        "then send it again, which freezes the corrected figures under the same number. Here the " +
        "client's province was missing when it was first sent, so it went out with no tax."
    );
    const orderPage = new OrderPage(page);
    const invoice = new InvoicePage(page);

    const order = await createClientAndOrder(page, journeyTag(testInfo), "Correction", {
      province: "",
      price: 100,
    });
    await orderPage.gotoBilling(order.orderId);
    const invoiceId = await orderPage.createInvoice();
    const number = (await invoice.number.innerText()).trim();

    // The draft says why there's no tax (U3)...
    await expect(invoice.warnings).toContainText("No tax on this invoice");
    // ...and it goes out without any anyway.
    await invoice.setStatus("Sent");
    await expect(invoice.warnings).toHaveCount(0);
    await invoice.expectTotals({ Total: "$100.00" });

    // The province is filled in afterwards: the sent invoice doesn't move.
    await setClientProvince(page, order.clientId, "BC");
    await invoice.goto(invoiceId);
    await invoice.expectTotals({ Total: "$100.00" });
    expect(await invoice.taxLabels()).toEqual([]);

    // Back to draft: live again, with BC's taxes.
    await invoice.setStatus("Draft");
    await invoice.expectStatus("Draft");
    await invoice.expectTotals({ "GST (5%)": "$5.00", "PST (7%)": "$7.00", Total: "$112.00" });

    // Sent again: frozen with them, under the same number.
    await invoice.setStatus("Sent");
    await expect(invoice.number).toHaveText(number);
    await invoice.expectTotals({ Total: "$112.00" });

    // Frozen for real: another province change leaves it alone.
    await setClientProvince(page, order.clientId, "AB");
    await invoice.goto(invoiceId);
    await invoice.expectTotals({ "GST (5%)": "$5.00", "PST (7%)": "$7.00", Total: "$112.00" });
    await orderPage.gotoBilling(order.orderId);
    await expect(orderPage.invoiceLink()).toHaveText(number);
    await expect(orderPage.totalRow("Order total")).toHaveText("$112.00");
  });
});

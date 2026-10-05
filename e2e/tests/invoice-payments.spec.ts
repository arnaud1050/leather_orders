import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { testData } from "../fixtures/testData";
import { createClientAndOrder, journeyTag } from "../lib/invoicing";
import { InvoicePage } from "../pages/InvoicePage";
import { OrderPage } from "../pages/OrderPage";

const instructions = testData.invoicingStudio.letterhead.paymentInstructions;

test.describe("Invoices: paying one off (D1, D2, D6, U4, U5)", () => {
  test("a deposit leaves a balance, the final payment makes it Paid, and the payment instructions go", async ({
    invoicingPage: page,
  }, testInfo) => {
    await feature("Invoicing");
    await description(
      "D1/D2: 'Paid' is derived from the payments recorded on the order — a deposit doesn't make an " +
        "invoice paid, covering the total does. D6/U4: nobody picks 'Paid' by hand. U5: payment " +
        "instructions are printed only while money is still owed."
    );
    const orderPage = new OrderPage(page);
    const invoice = new InvoicePage(page);

    // BC, $100: $112.00 with GST and PST.
    const order = await createClientAndOrder(page, journeyTag(testInfo), "Payments", {
      province: "BC",
      price: 100,
    });
    await orderPage.gotoBilling(order.orderId);
    const invoiceId = await orderPage.createInvoice();
    await invoice.setStatus("Sent");

    // "Paid" isn't on offer.
    await expect(invoice.statusSelect.locator("option")).toHaveText(["Draft", "Sent", "Void"]);

    // A deposit.
    await orderPage.gotoBilling(order.orderId);
    await orderPage.addPayment(50);
    await invoice.goto(invoiceId);
    await invoice.expectStatus("Sent");
    await invoice.expectTotals({ Total: "$112.00", Paid: "−$50.00", "Balance due": "$62.00" });
    await expect(invoice.paymentsReceived).toContainText("$50.00");
    await expect(invoice.paymentInstructions).toContainText(instructions);

    // The rest.
    await orderPage.gotoBilling(order.orderId);
    await orderPage.addPayment(62);
    await invoice.goto(invoiceId);
    await invoice.expectStatus("Paid");
    await invoice.expectTotals({ Paid: "−$112.00", "Balance due": "$0.00" });
    await expect(invoice.paymentInstructions).toHaveCount(0);

    // The order's Billing tab agrees.
    await orderPage.gotoBilling(order.orderId);
    await expect(page.locator(".detail-invoice .pill")).toHaveText("Paid");
  });
});

import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { testData } from "../fixtures/testData";
import { createClientAndOrder, journeyTag } from "../lib/invoicing";
import { InvoicePage, InvoicesListPage } from "../pages/InvoicePage";
import { OrderPage } from "../pages/OrderPage";

const instructions = testData.invoicingStudio.letterhead.paymentInstructions;

test.describe("Invoices: voiding one (F8, D3, N6, U5)", () => {
  test("a sent invoice voided keeps its number and figures, asks for no payment, and says Void everywhere", async ({
    invoicingPage: page,
  }, testInfo) => {
    await feature("Invoicing");
    await description(
      "Void is the only way to take an invoice back. It keeps its number (N6) and what it said, " +
        "stops asking for payment (U5), and reads Void on the invoice, the order and the list (D3)."
    );
    const orderPage = new OrderPage(page);
    const invoice = new InvoicePage(page);
    const list = new InvoicesListPage(page);

    const order = await createClientAndOrder(page, journeyTag(testInfo), "Void", {
      province: "BC",
      price: 100,
    });
    await orderPage.gotoBilling(order.orderId);
    await orderPage.createInvoice();
    const number = (await invoice.number.innerText()).trim();
    await invoice.setStatus("Sent");
    await expect(invoice.paymentInstructions).toContainText(instructions);

    await invoice.setStatus("Void");
    await invoice.expectStatus("Void");
    await expect(invoice.number).toHaveText(number);
    await invoice.expectTotals({ Total: "$112.00" });
    await expect(invoice.paymentInstructions).toHaveCount(0);

    await orderPage.gotoBilling(order.orderId);
    await expect(page.locator(".detail-invoice .pill")).toHaveText("Void");

    await list.goto();
    await expect(list.rowByNumber(number).locator(".pill")).toHaveText("Void");
    // Still invoiced: a void invoice doesn't put the order back on the to-do list.
    await expect(list.notInvoicedEntry(order.item)).toHaveCount(0);

    // Its number is used up for good: the next invoice gets a new one.
    const next = await createClientAndOrder(page, journeyTag(testInfo), "AfterVoid", {
      province: "BC",
      price: 100,
    });
    await orderPage.gotoBilling(next.orderId);
    await orderPage.createInvoice();
    await expect(invoice.number).not.toHaveText(number);
  });
});

import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { createClientAndOrder, journeyTag } from "../lib/invoicing";
import { InvoicePage, InvoicesListPage } from "../pages/InvoicePage";
import { OrderPage } from "../pages/OrderPage";

test.describe("Invoices: the to-do list and getting around (U6, U8, U10, MOD5)", () => {
  test("an order waits under Not invoiced yet until it has an invoice, and every link comes back where it started", async ({
    invoicingPage: page,
  }, testInfo) => {
    await feature("Invoicing");
    await description(
      "U8: the Invoices page's 'Not invoiced yet' list is the to-do the page exists for. U6/U10/MOD5: " +
        "the client and order links on an invoice work, and each back link returns to wherever the " +
        "invoice was opened from, worded for it."
    );
    const orderPage = new OrderPage(page);
    const invoice = new InvoicePage(page);
    const list = new InvoicesListPage(page);

    const order = await createClientAndOrder(page, journeyTag(testInfo), "Navigation", {
      province: "BC",
      price: 100,
    });

    // Waiting to be invoiced, with its total.
    await list.goto();
    const waiting = list.notInvoicedEntry(order.item);
    await expect(waiting).toContainText(order.clientName);
    await expect(waiting).toContainText("$112.00");

    // From the to-do list to the order, and back.
    await waiting.getByRole("link", { name: order.item }).click();
    await expect(orderPage.heading).toHaveText(order.item);
    await expect(orderPage.backLink()).toHaveText(/Back to invoices/);
    await orderPage.backLink().click();
    await expect(page).toHaveURL(/\/invoices$/);

    // Invoice it: it leaves the to-do list and joins the table.
    await orderPage.gotoBilling(order.orderId);
    const invoiceId = await orderPage.createInvoice();
    const number = (await invoice.number.innerText()).trim();
    await list.goto();
    await expect(list.notInvoicedEntry(order.item)).toHaveCount(0);
    const row = list.rowByNumber(number);
    await expect(row).toContainText(order.clientName);
    await expect(row).toContainText(order.item);
    await expect(row).toContainText("$112.00");

    // List -> invoice -> back to the list.
    await row.getByRole("link", { name: number }).click();
    await expect(invoice.number).toHaveText(number);
    await expect(invoice.backLink).toHaveText(/Back to invoices/);
    await invoice.backLink.click();
    await expect(page).toHaveURL(/\/invoices$/);

    // Order's Billing tab -> invoice -> back to the order.
    await orderPage.gotoBilling(order.orderId);
    await orderPage.invoiceLink().click();
    await expect(invoice.number).toHaveText(number);
    await expect(invoice.backLink).toHaveText(/Back to order/);
    await invoice.backLink.click();
    await expect(page).toHaveURL(new RegExp(`/orders/${order.orderId}/billing$`));

    // The invoice's client link opens the client, and comes back to the invoice.
    await invoice.goto(invoiceId);
    await invoice.doc.getByRole("link", { name: order.clientName }).click();
    await expect(page).toHaveURL(new RegExp(`/clients/${order.clientId}`));
    await page.locator(".back-link").click();
    await expect(page).toHaveURL(new RegExp(`/invoices/${invoiceId}$`));
  });
});

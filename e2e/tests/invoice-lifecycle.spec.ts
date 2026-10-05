import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { testData } from "../fixtures/testData";
import { createClientAndOrder, journeyTag } from "../lib/invoicing";
import { InvoicePage } from "../pages/InvoicePage";
import { OrderPage } from "../pages/OrderPage";
import { SettingsPage } from "../pages/SettingsPage";
import { gotoPath } from "../lib/nav";

const letterhead = testData.invoicingStudio.letterhead;

test.describe("Invoices: set up, draft, send, frozen (F1, F2, F6, OR7a)", () => {
  test("the letterhead reaches a draft, the draft follows the order, and once sent nothing moves it", async ({
    invoicingPage: page,
  }, testInfo) => {
    await feature("Invoicing");
    await description(
      "The whole life of an invoice in one pass. A draft is live (F1): it shows the letterhead saved " +
        "in Settings and follows the order's lines. Marking it sent freezes it (F2): the order's lines " +
        "lock (OR7a), even from a tab left open, and moving the client to another province afterwards " +
        "changes nothing it says (F6)."
    );
    const settings = new SettingsPage(page);
    const orderPage = new OrderPage(page);
    const invoice = new InvoicePage(page);

    // Set up. The seed already holds these values; saving them again through
    // the forms is what this checks, and leaves every other journey's
    // letterhead exactly as it was.
    await settings.gotoInvoicing();
    const details = page.locator('form[action="/settings/company"]');
    await details.locator('input[name="street"]').fill(letterhead.street);
    await details.locator('input[name="city"]').fill(letterhead.city);
    await details.locator('input[name="postal_code"]').fill(letterhead.postalCode);
    await details.locator('select[name="province"]').selectOption(letterhead.province);
    await details.locator('input[name="gst_number"]').fill(letterhead.gstNumber);
    await details.locator('input[name="pst_number"]').fill(letterhead.pstNumber);
    await details.getByRole("button", { name: "Save" }).click();
    await expect(page.locator(".save-notice", { hasText: "Company details saved." })).toBeVisible();

    const invoicingForm = page.locator('form[action="/settings/invoicing"]');
    await invoicingForm.locator('input[name="invoice_prefix"]').fill(letterhead.invoicePrefix);
    await invoicingForm.locator('textarea[name="payment_instructions"]').fill(letterhead.paymentInstructions);
    await invoicingForm.getByRole("button", { name: "Save" }).click();
    await expect(page.locator(".save-notice", { hasText: "Invoicing settings saved." })).toBeVisible();

    // A BC client, shipped: GST 5% + PST 7% on $200.
    const order = await createClientAndOrder(page, journeyTag(testInfo), "Lifecycle", {
      province: "BC",
      price: 200,
    });
    await orderPage.gotoBilling(order.orderId);
    await expect(orderPage.totalRow("Order total")).toHaveText("$224.00");

    // Create: a numbered draft carrying the letterhead.
    const invoiceId = await orderPage.createInvoice();
    await expect(invoice.number).toHaveText(new RegExp(`^${letterhead.invoicePrefix}-\\d{4}-\\d{4}$`));
    const number = (await invoice.number.innerText()).trim();
    await invoice.expectStatus("Draft");
    await expect(invoice.issuerRegistrations).toContainText(letterhead.gstNumber);
    await expect(invoice.issuerRegistrations).toContainText(letterhead.pstNumber);
    await expect(invoice.paymentInstructions).toContainText(letterhead.paymentInstructions);
    await invoice.expectTotals({ "GST (5%)": "$10.00", "PST (7%)": "$14.00", Total: "$224.00" });

    // Edit while a draft: it follows the order.
    await orderPage.gotoBilling(order.orderId);
    await orderPage.addLine("Brass hardware", 50);
    await invoice.goto(invoiceId);
    await invoice.expectTotals({
      Subtotal: "$250.00",
      "GST (5%)": "$12.50",
      "PST (7%)": "$17.50",
      Total: "$280.00",
    });

    // A second tab with the Billing tab open from before it's sent.
    const staleTab = await page.context().newPage();
    const stale = new OrderPage(staleTab);
    await stale.gotoBilling(order.orderId);

    // Send: frozen.
    await invoice.setStatus("Sent");
    await invoice.expectStatus("Sent");

    // The lines are locked now (OR7a): no way to add or delete one...
    await orderPage.gotoBilling(order.orderId);
    await expect(page.locator(".detail-lines .detail-note", { hasText: `Invoice ${number} has been sent` })).toContainText(
      "back to Draft first"
    );
    await expect(page.getByRole("button", { name: "Add line" })).toHaveCount(0);
    await expect(page.locator(".detail-lines .icon-btn--danger")).toHaveCount(0);

    // ...and the tab left open is refused, in red, where its button was.
    await stale.page.locator('.detail-lines input[name="description"]').fill("Extra strap");
    await stale.page.locator('.detail-lines input[name="unit_price"]').fill("100.00");
    await stale.page.getByRole("button", { name: "Add line" }).click();
    await expect(
      stale.page.locator('[data-notice-slot="line-items"] .save-notice--error', {
        hasText: "this order's lines can't change",
      })
    ).toBeVisible();
    await expect(stale.totalRow("Order total")).toHaveText("$280.00");
    await staleTab.close();

    // Moving the client to another province afterwards...
    await gotoPath(page, `/clients/${order.clientId}`);
    await page.locator('select[name="province"]').selectOption("AB");
    await page.getByRole("button", { name: "Save changes" }).click();
    await page.waitForURL((url) => url.pathname !== `/clients/${order.clientId}`, { waitUntil: "domcontentloaded" });

    // ...and the invoice still says exactly what was sent.
    await invoice.goto(invoiceId);
    await expect(invoice.number).toHaveText(number);
    await invoice.expectTotals({
      Subtotal: "$250.00",
      "GST (5%)": "$12.50",
      "PST (7%)": "$17.50",
      Total: "$280.00",
    });
    await expect(invoice.doc).not.toContainText("Extra strap");
  });
});

import { Page, TestInfo, expect } from "@playwright/test";
import { step } from "allure-js-commons";
import { gotoPath } from "./nav";
import { NewOrderPage } from "../pages/NewOrderPage";
import { isoDateOffset } from "../fixtures/testData";

/**
 * Setting up an invoice journey's own client and order, through the app's
 * real forms, in the invoicing studio (see `invoicingPage` in
 * fixtures/auth.fixture.ts).
 *
 * Every journey makes its own rows rather than sharing seeded ones: the
 * suite runs in parallel across three browsers, and two journeys moving
 * the same order through draft, sent and void at once would fail each
 * other. Names carry the browser project and a random tag, so they're
 * unique within a run and across reruns against a kept database.
 */

export interface JourneyOrder {
  orderId: number;
  clientId: number;
  clientName: string;
  item: string;
}

export interface JourneyOptions {
  /** A province code, "OUTSIDE" for Outside Canada, or "" for none. */
  province: string;
  price: number;
  delivery?: "pickup" | "shipped";
}

/** "Chromium 4821" — short, readable in a report, unique enough. */
export function journeyTag(testInfo: TestInfo): string {
  const project = testInfo.project.name.replace(/(^|-)(\w)/g, (_, dash, c) => (dash ? " " : "") + c.toUpperCase());
  return `${project} ${Math.floor(1000 + Math.random() * 9000)}`;
}

export async function createClientAndOrder(
  page: Page,
  tag: string,
  label: string,
  options: JourneyOptions,
): Promise<JourneyOrder> {
  const firstName = label;
  const lastName = tag;
  const item = `${label} bag ${tag}`;

  return await step(`Create client "${firstName} ${lastName}" with an order for $${options.price}`, async () => {
    const form = new NewOrderPage(page);
    await form.goto("/orders");
    await form.chooseNewClient();
    await form.newFirstNameInput.fill(firstName);
    await form.newLastNameInput.fill(lastName);
    await form.itemInput.fill(item);
    await form.dueInput.fill(isoDateOffset(14));
    await page.locator('input[name="price"]').fill(options.price.toFixed(2));
    await page.locator('select[name="status"]').selectOption("confirmed");
    await form.chooseDelivery(options.delivery ?? "shipped");
    await form.submit();
    await page.waitForURL(/\/orders(\?|$)/, { waitUntil: "domcontentloaded" });

    await page.getByRole("link", { name: item, exact: true }).click();
    await page.waitForURL(/\/orders\/\d+/, { waitUntil: "domcontentloaded" });
    const orderId = Number(new URL(page.url()).pathname.split("/")[2]);

    const clientLink = page.locator(".detail-header .detail-subtitle a");
    const clientId = Number(new URL(await clientLink.getAttribute("href") ?? "", page.url()).pathname.split("/")[2]);

    if (options.province) {
      await gotoPath(page, `/clients/${clientId}`);
      await page.locator('select[name="province"]').selectOption(options.province);
      await page.getByRole("button", { name: "Save changes" }).click();
      // Saving redirects to the page's return_to (the timeline by default).
      await page.waitForURL((url) => url.pathname !== `/clients/${clientId}`, { waitUntil: "domcontentloaded" });
      await gotoPath(page, `/clients/${clientId}`);
      await expect(page.locator('select[name="province"]')).toHaveValue(options.province);
    }

    return { orderId, clientId, clientName: `${firstName} ${lastName}`, item };
  });
}

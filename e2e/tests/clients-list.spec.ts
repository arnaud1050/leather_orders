import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { ClientsListPage } from "../pages/ClientsListPage";
import { ClientPage } from "../pages/ClientPage";
import { clientFullName, clientByKey, clientLifetimeValue, formatMoney } from "../fixtures/testData";
import { gotoPath } from "../lib/nav";

test.describe("Clients list — orders filter reaches all three states (LST5, LST5a)", () => {
  test("both groups show by default", async ({ adminPage }) => {
    await feature("Clients");
    await description(
      "LST5a: both the 'With orders' and 'No orders' groups show by default — nothing is hidden until " +
        "one of the two legend buttons is clicked."
    );
    const clients = new ClientsListPage(adminPage);
    await clients.goto();
    // 6 seeded clients: 5 have at least one order, Hedy Lamarr has none.
    await expect(clients.clientCount).toHaveText("6");
    await expect(clients.rowByName("Hedy Lamarr")).toBeVisible();
    await expect(clients.rowByName("Ada Lovelace")).toBeVisible();
  });

  test("'No orders' isolates Hedy Lamarr alone", async ({ adminPage }) => {
    await feature("Clients");
    await description(
      "LST5: hiding 'With orders' leaves only clients with zero orders visible — Hedy Lamarr is the one " +
        "seeded client in that group."
    );
    const clients = new ClientsListPage(adminPage);
    await clients.goto();
    await clients.toggleOrderGroupFilter("with");
    await expect(clients.legendButton("with")).toHaveClass(/legend__item--off/);
    await expect(clients.clientCount).toHaveText("1");
    await expect(clients.rowByName("Hedy Lamarr")).toBeVisible();
    await expect(clients.rowByName("Ada Lovelace")).toBeHidden();
  });

  test("'With orders' hides only Hedy Lamarr", async ({ adminPage }) => {
    await feature("Clients");
    await description("LST5: hiding 'No orders' removes exactly the one client with zero orders, Hedy Lamarr.");
    const clients = new ClientsListPage(adminPage);
    await clients.goto();
    await clients.toggleOrderGroupFilter("none");
    await expect(clients.clientCount).toHaveText("5");
    await expect(clients.rowByName("Hedy Lamarr")).toBeHidden();
  });
});

test.describe("Clients list — returning clients and lifetime value (CL2, CL2a)", () => {
  test("returning count reflects only clients with 2+ orders", async ({ adminPage }) => {
    await feature("Clients");
    await description(
      "CL2: Client.is_returning = len(orders) >= 2, computed on every read. Of the 6 seeded clients, " +
        "only Ada has two orders."
    );
    const clients = new ClientsListPage(adminPage);
    await clients.goto();
    // Only Ada has two orders.
    await expect(clients.returningCount).toHaveText("1");
  });

  test("a cancelled order does not count toward lifetime value", async ({ adminPage }) => {
    await feature("Clients");
    await description(
      "CL2a: lifetime_value sums Order.total excluding cancelled orders — work that was called off was " +
        "never business done. Katherine's only order is cancelled ($200), so her lifetime value must read " +
        "$0.00 despite the order existing and showing on /orders."
    );
    const clients = new ClientsListPage(adminPage);
    await clients.goto();
    const katherine = clientByKey("katherine");
    const row = clients.rowByName(clientFullName(katherine));
    // Katherine's only order is cancelled ($200) — CL2a says it must not
    // inflate lifetime value, so hers should read $0.00 despite the order
    // existing and showing on /orders.
    await expect(row.locator("td.is-numeric").nth(1)).toHaveText(formatMoney(clientLifetimeValue(katherine)));
  });

  test("Ada's lifetime value sums both of her orders", async ({ adminPage }) => {
    await feature("Clients");
    await description(
      "CL2: lifetime_value = sum(o.total for o in orders if o.status != 'cancelled'). Ada's two orders " +
        "(neither cancelled) should sum to her displayed lifetime value."
    );
    const clients = new ClientsListPage(adminPage);
    await clients.goto();
    const ada = clientByKey("ada");
    const row = clients.rowByName(clientFullName(ada));
    await expect(row.locator("td.is-numeric").nth(1)).toHaveText(formatMoney(clientLifetimeValue(ada)));
  });
});

test.describe("Client hide/unhide (CL17-CL19)", () => {
  // Marked fixme rather than left as a hollow pass: the hide control lives
  // in a `.detail-lifecycle` block on the full client page behind a
  // confirm dialog (see docs/views.md), and this scaffold hasn't inspected
  // that page's real markup yet — filling this in needs real selectors,
  // This test hides a seeded client and puts them back at the end. Radia
  // Perlman exists in the fixture for exactly this and nothing else — don't
  // assert on her from another test, or the two will race under parallel
  // workers (they share one server and one database).
  test("hiding a client moves them to the archive and leaves their order alone", async ({
    adminPage,
  }) => {
    await feature("Clients");
    await description(
      "CL17-CL19: hiding is roster-scope only. The client comes off /clients and appears in the " +
        "?hidden=1 archive, but their order keeps rendering on /orders under their name — CL18 is " +
        "explicit that hiding must not touch a single order, payment, invoice or analytics figure. " +
        "There is deliberately no Delete beside it (CL17): Order, Invoice and EmailThread all " +
        "reference a client, so a deleted row would leave every one of them pointing at nobody."
    );
    const clients = new ClientsListPage(adminPage);
    const clientPage = new ClientPage(adminPage);
    const radia = clientByKey("radia");
    const radiaName = clientFullName(radia);
    const radiaOrder = radia.orders[0].item;

    await clients.goto();
    await clients.rowByName(radiaName).getByRole("link", { name: radiaName, exact: true }).click();
    await expect(adminPage).toHaveURL(/\/clients\/\d+/);

    // CL17 — one boolean, both directions, and no Delete anywhere near it.
    await expect(adminPage.locator(".detail-lifecycle")).not.toContainText("Delete");

    try {
      await clientPage.hide();

      // Off the roster...
      await clients.goto();
      await expect(clients.rowByName(radiaName)).toHaveCount(0);
      // ...and the archive link appears now that there's something in it (CL19).
      await expect(clients.archiveLink()).toBeVisible();

      await clients.goto(true);
      await expect(clients.rowByName(radiaName)).toBeVisible();
      // CL19 — the archive is only hidden clients, never a mixed list.
      await expect(clients.rowByName("Ada Lovelace")).toHaveCount(0);
      // ...and it omits "+ Add client": nobody means to add someone straight
      // to the hidden list.
      await expect(clients.addClientButton).toHaveCount(0);

      // CL18 — the order is untouched and still carries her name.
      await gotoPath(adminPage, "/orders");
      const orderRow = adminPage.locator("#orders-table tbody tr").filter({ hasText: radiaOrder });
      await expect(orderRow).toBeVisible();
      await expect(orderRow).toContainText(radiaName);
    } finally {
      // Put her back however the assertions above went, so a failure here
      // doesn't leave the fixture altered for whatever runs next.
      await clients.goto(true);
      await clients.rowByName(radiaName).getByRole("link", { name: radiaName, exact: true }).click();
      await clientPage.unhide();
    }

    await clients.goto();
    await expect(clients.rowByName(radiaName)).toBeVisible();
  });
});

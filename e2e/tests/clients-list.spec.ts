import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { ClientsListPage } from "../pages/ClientsListPage";
import { clientFullName, clientByKey, clientLifetimeValue, formatMoney } from "../fixtures/testData";

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
  // not guesses. Steps once it's implemented:
  //   1. open Radia Perlman's full client page, click the hide action,
  //      confirm the dialog
  //   2. assert redirect back to /clients with Radia absent from the roster
  //   3. assert she appears on /clients?hidden=1
  //   4. assert her order (Laptop Sleeve) is unchanged on /orders and on
  //      her own Orders tab (CL18 — hiding must not touch a single order,
  //      payment, invoice or analytics figure)
  //   5. un-hide her again at the end, so this fixture client is back to
  //      its seeded state for any test that runs after this one
  test.fixme(
    "hiding Radia Perlman removes her from the roster and lists her in the archive, untouched otherwise",
    async ({ adminPage }) => {
      await feature("Clients");
      await description(
        "CL17-CL19: hiding a client is roster-scope only — it removes them from /clients and the new-order " +
          "picker, and nothing else. Their orders, payments, invoices and every analytics figure must stay " +
          "bit-identical."
      );
      const clients = new ClientsListPage(adminPage);
      await clients.goto();
      const radiaName = clientFullName(clientByKey("radia"));
      await clients.rowByName(radiaName).getByRole("link", { name: radiaName }).click();
      await expect(adminPage).toHaveURL(/\/clients\/\d+/);
    }
  );
});

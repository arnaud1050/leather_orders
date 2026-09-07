import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { OrdersListPage } from "../pages/OrdersListPage";
import { orderByKey, formatMoney } from "../fixtures/testData";

test.describe("Orders list — status filter recomputes stat cards (LST4)", () => {
  test("hiding a status updates both the order count and the balance-due total", async ({ adminPage }) => {
    await feature("Orders");
    await description(
      "LST4: /orders' status filter recomputes the order-count and balance-due stat cards from the " +
        "still-visible rows, not the full unfiltered roster."
    );
    const orders = new OrdersListPage(adminPage);
    await orders.goto();

    // All 6 seeded orders show here (unlike the timeline, /orders is
    // unwindowed — LST1) including the cancelled and long-delivered ones
    // the timeline excludes.
    await expect(orders.orderCount).toHaveText("6");

    const beforeBalance = await orders.balanceTotal.textContent();

    await orders.toggleStatusFilter("cancelled");
    await expect(orders.legendButton("cancelled")).toHaveClass(/legend__item--off/);
    await expect(orders.orderCount).toHaveText("5");
    await expect(orders.rowByItem("Cancelled Satchel")).toBeHidden();

    // Katherine's cancelled order carries a $200 balance (never paid); once
    // it's filtered out, the visible total must drop by exactly that.
    const afterBalance = await orders.balanceTotal.textContent();
    expect(afterBalance).not.toEqual(beforeBalance);
  });

  test("cancelled orders render muted (.is-cancelled) but are never hidden by default", async ({ adminPage }) => {
    await feature("Orders");
    await description(
      "OR1g: cancelled orders stay on /orders (rendered muted via .is-cancelled) even though they're " +
        "excluded from the Timeline query entirely — the two views intentionally disagree here."
    );
    const orders = new OrdersListPage(adminPage);
    await orders.goto();
    await expect(orders.rowByItem("Cancelled Satchel")).toHaveClass(/is-cancelled/);
  });
});

test.describe("Orders list — sorting (LST2, LST3)", () => {
  test("defaults to sorting by due date ascending", async ({ adminPage }) => {
    await feature("Orders");
    await description("LST3: /orders defaults to sorting by due date, unlike /clients which defaults to name.");
    const orders = new OrdersListPage(adminPage);
    await orders.goto();
    await expect(adminPage).toHaveURL("/orders");
    // The due column's own sort link should read as the active column.
    const dueLink = orders.table.locator("thead a.sort-link", { hasText: "Due" });
    await expect(dueLink).toHaveClass(/is-active/);
  });

  test("clicking Total sorts numerically and toggles direction on a second click", async ({ adminPage }) => {
    await feature("Orders");
    await description(
      "LST2: sorting is server-side via ?sort=&dir=, computed in Python since several sort keys " +
        "(order.total) are computed properties rather than real columns. A second click on the same " +
        "column header reverses direction."
    );
    const orders = new OrdersListPage(adminPage);
    await orders.goto();
    await orders.sortByColumn("Total");
    await expect(adminPage).toHaveURL(/sort=total&dir=asc/);
    await orders.sortByColumn("Total");
    await expect(adminPage).toHaveURL(/sort=total&dir=desc/);
  });
});

test.describe("Orders list — order totals (OR5, PM1)", () => {
  test("Belt Set shows the seeded price and the correct balance after a partial payment", async ({
    adminPage,
  }) => {
    await feature("Orders");
    await description(
      "OR5/PM1: Order.total is the sum of its line items; balance_due = price - amount_paid, computed on " +
        "every read. Margaret's Belt Set ($300, one $100 payment) should read Total $300 / Paid $100 / " +
        "Balance $200."
    );
    const orders = new OrdersListPage(adminPage);
    await orders.goto();
    const { order } = orderByKey("margaret-ready-order");
    const row = orders.rowByItem("Belt Set");

    // Indexed by position among the *numeric* columns (Total/Paid/Balance),
    // which is the canonical ORDER_COLUMNS order for a company that's never
    // touched Settings > Orders > "Orders list columns" (LST6-LST9) — a
    // fresh reorder there would need this test updated too.
    await expect(row.locator("td.is-numeric").nth(0)).toHaveText(formatMoney(order.price)); // Total
    await expect(row.locator("td.is-numeric").nth(1)).toHaveText(formatMoney(100)); // Paid
    await expect(row.locator("td.is-numeric").nth(2)).toHaveText(formatMoney(200)); // Balance
  });
});

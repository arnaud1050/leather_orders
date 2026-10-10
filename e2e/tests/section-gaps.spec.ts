import { Page } from "@playwright/test";
import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { LoginPage } from "../pages/LoginPage";
import { gotoPath } from "../lib/nav";

/**
 * docs/design.md, "Vertical rhythm" — sections sit exactly 40px apart, on
 * every page, whatever each one ends with.
 *
 * The gap is measured the way it reads: from the lowest visible thing above
 * a section heading (text, a control, a bordered or filled box) to the top
 * of that heading. Margins are invisible, so this catches the failure that
 * kept recurring — a form's 40px, a note's 12px or a grid's 16px left under
 * a section's last line and added to the section's own 40px (48–80px gaps)
 * — however the page's markup is nested. The first heading on a page is
 * skipped: what sits above it is the page header, a different spacing.
 *
 * The page lists are deliberately broad: a new page belongs here, and a
 * page that 404s (a feature switched off for the seeded studio) is skipped
 * rather than failed.
 */

const SECTION_GAP = 40;

/** Every section heading after the first, with the visible gap above it. */
async function sectionGaps(page: Page): Promise<{ heading: string; gap: number; above: string }[]> {
  // Same-origin stylesheets loaded (the fonts, cross-origin, don't move margins).
  await page.waitForFunction(() =>
    [...document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')]
      .filter((l) => l.href.startsWith(location.origin))
      .every((l) => l.sheet !== null && l.sheet.cssRules.length > 0)
  );
  return await page.evaluate(() => {
    const root = document.querySelector(".ledger") ?? document.querySelector("main") ?? document.body;
    const visible = (e: Element) => {
      const r = e.getBoundingClientRect();
      if (r.height < 1 || r.width < 1) return false;
      const cs = getComputedStyle(e);
      if (cs.visibility === "hidden" || cs.position === "fixed" || cs.position === "absolute") return false;
      return !e.closest("dialog:not([open]), [hidden]");
    };
    const drawn = (e: Element) => {
      if (/^(IMG|INPUT|SELECT|TEXTAREA|BUTTON|svg|HR|CANVAS|TABLE)$/.test(e.tagName)) return true;
      const cs = getComputedStyle(e);
      if (parseFloat(cs.borderTopWidth) || parseFloat(cs.borderBottomWidth)) return true;
      if (cs.backgroundColor !== "rgba(0, 0, 0, 0)" && cs.backgroundColor !== "transparent") return true;
      return [...e.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && n.textContent!.trim() !== "");
    };
    const content = [...root.querySelectorAll("*")].filter(
      (e) => (!e.closest("svg") || e.tagName === "svg") && visible(e) && drawn(e)
    );
    const rows: { heading: string; gap: number; above: string }[] = [];
    for (const h of [...root.querySelectorAll("h2")].filter(visible)) {
      const top = h.getBoundingClientRect().top;
      let lowest = -Infinity;
      let above: Element | null = null;
      for (const e of content) {
        if (e === h || e.contains(h) || h.contains(e)) continue;
        if (!(h.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_PRECEDING)) continue;
        const bottom = e.getBoundingClientRect().bottom;
        if (bottom <= top + 1 && bottom > lowest) {
          lowest = bottom;
          above = e;
        }
      }
      if (above) {
        rows.push({
          heading: h.textContent!.trim().slice(0, 60),
          gap: Math.round(top - lowest),
          above: `${above.tagName.toLowerCase()}.${[...above.classList].join(".")}`,
        });
      }
    }
    return rows.slice(1);
  });
}

/** Visits each path and collects every gap that isn't 40px. */
async function offRhythm(page: Page, paths: string[]): Promise<string[]> {
  const problems: string[] = [];
  for (const path of paths) {
    const response = await page.goto(path, { waitUntil: "domcontentloaded" });
    if (response?.status() === 404) continue;
    for (const row of await sectionGaps(page)) {
      if (Math.abs(row.gap - SECTION_GAP) > 1) {
        problems.push(`${path}: "${row.heading}" is ${row.gap}px under ${row.above}`);
      }
    }
  }
  return problems;
}

/** The first `/prefix/<id>` link on a list page, for its detail pages. */
async function firstId(page: Page, listPath: string, prefix: string): Promise<string | null> {
  await gotoPath(page, listPath);
  const hrefs = await page
    .locator(`a[href^="${prefix}/"]`)
    .evaluateAll((links) => links.map((a) => a.getAttribute("href") ?? ""));
  const match = hrefs.map((h) => h.match(new RegExp(`^${prefix}/(\\d+)$`))).find(Boolean);
  return match ? match[1] : null;
}

test.describe("Sections sit 40px apart on every page (design.md, Vertical rhythm)", () => {
  test("every studio page", async ({ adminPage }) => {
    await feature("Design");
    await description(
      "Every section heading after a page's first sits exactly 40px below the content above it, " +
        "on every page a studio user reaches, whatever form, note, list or grid ends the section."
    );
    const paths = [
      "/", "/calendar", "/week", "/orders", "/orders/new", "/clients", "/clients/new",
      "/inventory", "/invoices", "/analytics", "/mail/leads",
      "/settings/general", "/settings/clients", "/settings/orders", "/settings/inventory",
      "/settings/invoicing", "/settings/integrations", "/settings/ai", "/settings/account",
      "/settings/showcase", "/showcase", "/showcase/items/new",
      "/help/orders", "/help/clients", "/privacy", "/terms",
    ];
    const order = await firstId(adminPage, "/orders", "/orders");
    if (order) {
      paths.push(`/orders/${order}`, `/orders/${order}/billing`, `/orders/${order}/materials`);
    }
    const client = await firstId(adminPage, "/clients", "/clients");
    if (client) {
      paths.push(`/clients/${client}`, `/clients/${client}/orders`, `/clients/${client}/emails`);
    }
    const invoice = await firstId(adminPage, "/invoices", "/invoices");
    if (invoice) paths.push(`/invoices/${invoice}`);

    expect(await offRhythm(adminPage, paths)).toEqual([]);
  });

  test("every platform admin page", async ({ browser }) => {
    await feature("Design");
    await description("The same 40px between sections on the platform admin's pages.");
    // The platform admin seed_e2e_data.py's ensure_platform_admin() creates
    // on the scratch database, with the bootstrap defaults (CLAUDE.md, Auth).
    const context = await browser.newContext();
    const page = await context.newPage();
    try {
      const login = new LoginPage(page);
      await login.goto();
      await login.login(
        process.env.PLATFORM_ADMIN_EMAIL ?? "platform@example.invalid",
        process.env.PLATFORM_ADMIN_PASSWORD ?? "changeme"
      );
      await page.waitForURL(/\/admin/);
      const paths = ["/admin/companies", "/admin/platform-admins", "/admin/settings", "/admin/usage"];
      const company = await firstId(page, "/admin/companies", "/admin/companies");
      if (company) paths.push(`/admin/companies/${company}`);

      expect(await offRhythm(page, paths)).toEqual([]);
    } finally {
      await context.close();
    }
  });
});

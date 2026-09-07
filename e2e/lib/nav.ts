import { Page } from "@playwright/test";

/**
 * Every page here pulls Google Fonts cross-origin (base.html's
 * `<link href="https://fonts.googleapis.com/...">`). Playwright's default
 * `page.goto()` waits for the "load" event, which waits for that external
 * stylesheet too — slow at best, and in a network-restricted environment
 * (a CI runner with no outbound internet, a sandboxed dev box) it can hang
 * the navigation until Playwright's own timeout fires. Nothing in these
 * tests depends on the font having actually loaded, so every goto() in
 * this project waits for "domcontentloaded" instead via this helper.
 */
export async function gotoPath(page: Page, path: string): Promise<void> {
  await page.goto(path, { waitUntil: "domcontentloaded" });
}

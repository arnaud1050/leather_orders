import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { SettingsPage } from "../pages/SettingsPage";

/**
 * Invoice appearance — a logo the browser refuses before uploading.
 *
 * `static/assets/js/invoice-appearance.js` checks a chosen file's type and
 * size and, when it won't do, writes the reason into the section's
 * `[data-logo-error]` box and sends nothing. No test covered that script,
 * and the box now uses the red save-notice style (MOD8) — so this checks
 * the message appears, in the section, in red, and that no upload went out.
 *
 * Nothing here uploads anything, so the company keeps no logo and the
 * other browser projects see the same "Add logo" tile.
 */

const RED = "rgb(194, 59, 52)";
const LOGO_MAX_BYTES = 10 * 1024 * 1024; // billing/config.py's default

test.describe("Invoice appearance — a refused logo is explained in its section", () => {
  test("a file that isn't an image is refused in red, and nothing is uploaded", async ({
    adminPage,
  }) => {
    await feature("Invoicing");
    await description(
      "Choosing a text file as the logo: the section shows a red message saying a logo needs to be a " +
        "PNG or JPEG, the file input is cleared, and no request reaches the upload route."
    );
    const settings = new SettingsPage(adminPage);
    const uploads: string[] = [];
    adminPage.on("request", (request) => {
      if (request.method() === "POST" && request.url().includes("/settings/invoicing/logo")) {
        uploads.push(request.url());
      }
    });

    await settings.gotoInvoicing();
    await expect(settings.logoError()).toBeHidden();
    await settings.logoInput().setInputFiles({
      name: "logo.txt", mimeType: "text/plain", buffer: Buffer.from("not an image"),
    });

    const error = settings.logoError();
    await expect(error).toBeVisible();
    await expect(error).toHaveText("A logo needs to be a PNG or JPEG image.");
    await expect(error).toHaveClass(/save-notice--error/);
    await expect(error).toHaveCSS("border-left-color", RED);
    await expect(
      adminPage.locator("section").filter({ has: adminPage.getByRole("heading", { name: "Invoice appearance" }) })
        .locator("[data-logo-error]")
    ).toBeVisible();
    await expect(settings.logoInput()).toHaveValue("");
    expect(uploads, "a refused file must never be sent").toEqual([]);
  });

  test("a logo over the size limit is refused before it uploads", async ({ adminPage }) => {
    await feature("Invoicing");
    await description(
      "A PNG one byte over the limit: refused in the browser with the limit named, so a 10 MB upload " +
        "isn't sent only for the server to turn it down."
    );
    const settings = new SettingsPage(adminPage);
    const uploads: string[] = [];
    adminPage.on("request", (request) => {
      if (request.method() === "POST" && request.url().includes("/settings/invoicing/logo")) {
        uploads.push(request.url());
      }
    });

    await settings.gotoInvoicing();
    const max = Number(await settings.logoInput().getAttribute("data-max-bytes"));
    expect(max, "the page should state the limit it enforces").toBe(LOGO_MAX_BYTES);
    await settings.logoInput().setInputFiles({
      name: "logo.png", mimeType: "image/png", buffer: Buffer.alloc(max + 1),
    });

    const error = settings.logoError();
    await expect(error).toHaveText("That file is too large. A logo can be up to 10 MB.");
    await expect(error).toHaveCSS("border-left-color", RED);
    await expect(settings.logoInput()).toHaveValue("");
    expect(uploads).toEqual([]);
  });
});

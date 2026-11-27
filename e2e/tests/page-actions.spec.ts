import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { gotoPath } from "../lib/nav";

/**
 * CO4r: the Content-Security-Policy runs no inline on*= handler, so the
 * "are you sure?" a destructive form used to ask with onsubmit is now a
 * data-confirm attribute, read by static/assets/js/page-actions.js. Only a
 * real browser can show the dialog actually appears and that cancelling it
 * posts nothing.
 *
 * The seeded Gmail account is the one form with a confirmation the suite
 * can reach. The dialog is always *dismissed*: accepting would disconnect
 * the account, which calls Google to revoke its token.
 */
test.describe("Confirm before a destructive post (CO4r)", () => {
  test("Disconnect asks first, and cancelling leaves the account connected", async ({ adminPage }) => {
    await feature("Security");
    await description(
      "CO4r: data-confirm on a form asks before posting, from a script file rather than an inline " +
        "onsubmit the CSP would block. Dismissing the dialog must stop the post."
    );
    await gotoPath(adminPage, "/settings/integrations");
    const disconnect = adminPage.getByRole("button", { name: "Disconnect" }).first();
    await expect(disconnect).toBeVisible();

    const posts: string[] = [];
    adminPage.on("request", (request) => {
      if (request.method() === "POST") posts.push(request.url());
    });
    let asked = "";
    adminPage.once("dialog", async (dialog) => {
      asked = dialog.message();
      await dialog.dismiss();
    });

    await disconnect.click();

    await expect.poll(() => asked).toMatch(/^Disconnect .+\? Access is revoked at Google/);
    expect(posts).toEqual([]);
    await expect(adminPage).toHaveURL(/\/settings\/integrations/);
    await expect(adminPage.getByRole("button", { name: "Disconnect" }).first()).toBeVisible();
  });
});

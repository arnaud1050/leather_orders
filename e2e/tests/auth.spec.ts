import { test, expect } from "../fixtures/auth.fixture";
import { feature, description } from "allure-js-commons";
import { LoginPage } from "../pages/LoginPage";
import { BasePage } from "../pages/BasePage";
import { testData } from "../fixtures/testData";
import { gotoPath } from "../lib/nav";

// Uses the plain Playwright `page` fixture (not `adminPage`) for the two
// tests below, since they're testing the login flow itself rather than
// something that needs to already be logged in.
test.describe("Sign in (CO4)", () => {
  test("rejects an invalid password without saying which field was wrong", async ({ page }) => {
    await feature("Auth");
    await description(
      "CO4: a wrong password is refused with a generic error, and the request stays on /login rather " +
        "than redirecting — the message doesn't say whether the email or the password was the problem."
    );
    const login = new LoginPage(page);
    await login.goto();
    await login.login(testData.adminUser.email, "not-the-real-password");
    await login.expectError();
    await expect(page).toHaveURL(/\/login/);
  });

  test("signs in and lands on the timeline", async ({ page }) => {
    await feature("Auth");
    await description("CO4/CO4g: a valid email+password signs a tenant user in and lands them on the Timeline.");
    const login = new LoginPage(page);
    await login.goto();
    await login.login(testData.adminUser.email, testData.adminUser.password);
    await expect(page).toHaveURL("/");
    await expect(page.locator("h1")).toHaveText("Timeline");
  });

  test("an unauthenticated request to a protected route redirects to /login?next=...", async ({ page }) => {
    await feature("Auth");
    await description(
      "CO4: every view route requires @login_required. Hitting one signed-out redirects to " +
        "/login?next=<the original path>, so a successful sign-in can bounce back to where the user meant to go."
    );
    await gotoPath(page, "/orders");
    await expect(page).toHaveURL(/\/login\?next=%2Forders/);
  });
});

test.describe("Forced password change (CO4h)", () => {
  test("a user with must_change_password redirects everywhere except Settings > Account", async ({
    freshUserPage,
  }) => {
    await feature("Auth");
    await description(
      "CO4h: a user provisioned with an operator-assigned password (must_change_password=True) is redirected " +
        "to Settings > Account from every route except that page itself, /settings/account/password, " +
        "/logout, the legal pages and static files — until they change it."
    );
    // freshUserPage is already logged in — see fixtures/auth.fixture.ts.
    // Deliberately not completing the change here: this is the one seeded
    // user carrying the flag, and changing the password would break any
    // other test that still expects the seeded credentials to work.
    await gotoPath(freshUserPage, "/orders");
    await expect(freshUserPage).toHaveURL(/\/settings\/account/);

    await gotoPath(freshUserPage, "/clients");
    await expect(freshUserPage).toHaveURL(/\/settings\/account/);

    // The settings/account page itself, and logout, must NOT redirect —
    // otherwise there'd be no way to actually clear the flag.
    await gotoPath(freshUserPage, "/settings/account");
    await expect(freshUserPage).toHaveURL(/\/settings\/account/);
  });

  // TODO: a dedicated test that actually completes the change-password
  // form for this user (current password + new password twice), asserts
  // the redirect loop stops afterward, and — because this consumes the
  // seeded credentials — is the LAST test in the suite allowed to touch
  // `freshUserPage`. Consider seeding a second "fresh" user in
  // seed/e2e-data.json if you want this to run safely alongside other
  // tests instead of being order-dependent.
});

test.describe("Log out", () => {
  test("ends the session for subsequent requests", async ({ adminPage }) => {
    await feature("Auth");
    await description(
      "Logging out ends the Flask-Login session — a subsequent request to a protected route must " +
        "redirect to /login?next=..., not silently continue as the now-logged-out user."
    );
    const nav = new BasePage(adminPage);
    await gotoPath(adminPage, "/");
    await nav.logOut();
    await expect(adminPage).toHaveURL(/\/login/);

    await gotoPath(adminPage, "/orders");
    await expect(adminPage).toHaveURL(/\/login\?next=%2Forders/);
  });
});

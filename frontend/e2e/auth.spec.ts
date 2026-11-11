import { expect, test } from "@playwright/test";

import { signIn, signedIn, submitLogin } from "./helpers";

test.describe("authentication", () => {
  test("signed-out visitors are sent to login and returned after signing in", async ({ page }) => {
    await page.goto("/tickets/t1");
    await expect(page).toHaveURL(/\/login\?next=%2Ftickets%2Ft1/);
    // Already on /login?next=...: submit in place so the return path survives.
    await submitLogin(page);
    await expect(page).toHaveURL(/\/tickets\/t1$/);
  });

  test("a wrong password shows one generic message", async ({ page }) => {
    await signIn(page, "wrong");
    await expect(page.getByRole("alert").filter({ hasText: "Wrong workspace" })).toContainText(
      "Wrong workspace, email or password.",
    );
    await expect(page).toHaveURL(/\/login/);
  });

  test("the session token is never readable by page JavaScript", async ({ page, context }) => {
    await signedIn(page);
    expect(await page.evaluate(() => document.cookie)).not.toContain("cdesk_session");
    const cookies = await context.cookies();
    const session = cookies.find((c) => c.name === "cdesk_session");
    expect(session?.httpOnly).toBe(true);
  });

  test("an expired session returns the user to login", async ({ page, context }) => {
    await signedIn(page);
    await context.clearCookies();
    await page.reload();
    await expect(page).toHaveURL(/\/login/);
  });

  test("unknown pages show the branded 404", async ({ page }) => {
    await signedIn(page);
    await page.goto("/definitely-not-a-page");
    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  });
});

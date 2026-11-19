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

test.describe("self-service sign-up", () => {
  test("create a workspace, confirm the code, then sign in with it prefilled", async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("link", { name: "Create a workspace" }).click();
    await expect(page).toHaveURL(/\/signup$/);

    await page.getByLabel("Workspace name").fill("Acme Support");
    await expect(page.getByLabel("Workspace address")).toHaveValue("acme-support");
    await page.getByLabel("Workspace address").fill("acme");
    await page.getByLabel("Your name").fill("Ada Lovelace");
    await page.getByLabel("Email").fill("ada@example.com");
    await page.getByLabel("Password", { exact: true }).fill("correct-horse");
    await page.getByRole("button", { name: "Create workspace" }).click();

    await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
    await expect(page.getByLabel("6 digit code")).toBeFocused();
    await page.getByLabel("6 digit code").fill("123 456");
    await page.getByRole("button", { name: "Verify" }).click();

    await expect(page).toHaveURL(/\/login\?workspace=acme/);
    await expect(page.getByLabel("Workspace")).toHaveValue("acme");
    await page.getByLabel("Email").fill("ada@example.com");
    await page.getByLabel("Password", { exact: true }).fill("correct-horse");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/tickets/);
  });
});

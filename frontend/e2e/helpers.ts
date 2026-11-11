import { expect, type Page } from "@playwright/test";

export const CREDENTIALS = {
  workspace: "acme",
  email: "ada@example.com",
  password: "correct-horse",
};

export async function submitLogin(page: Page, password = CREDENTIALS.password) {
  await page.getByLabel("Workspace").fill(CREDENTIALS.workspace);
  await page.getByLabel("Email").fill(CREDENTIALS.email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

export async function signIn(page: Page, password = CREDENTIALS.password) {
  await page.goto("/login");
  await submitLogin(page, password);
}

export async function signedIn(page: Page) {
  await signIn(page);
  await expect(page).toHaveURL(/\/tickets/);
}

export async function resetBackend() {
  const res = await fetch("http://localhost:3101/__reset", { method: "POST" });
  if (!res.ok) throw new Error(`mock backend reset failed: ${res.status}`);
}

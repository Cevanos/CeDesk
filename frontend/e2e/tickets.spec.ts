import { expect, test, type Page } from "@playwright/test";

import { resetBackend, signedIn } from "./helpers";

/** The new-ticket page renders more than one "Create ticket" button, so use the sidebar one. */
function createButton(page: Page) {
  return page
    .getByRole("complementary", { name: "Ticket settings" })
    .getByRole("button", { name: "Create ticket" });
}

test.describe("tickets", () => {
  test.beforeEach(async ({ page }) => {
    await resetBackend();
    await signedIn(page);
  });

  test("the queue lists tickets with counts", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Tickets", level: 1 })).toBeVisible();
    await expect(page.getByRole("link", { name: /Cannot log in/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /Open/ })).toHaveAttribute("aria-selected", "true");
  });

  test("filters live in the URL", async ({ page }) => {
    await page.getByRole("tab", { name: /Resolved/ }).click();
    await expect(page).toHaveURL(/status=resolved/);
    await page.reload();
    await expect(page.getByRole("tab", { name: /Resolved/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  test("creates a ticket and lands on it", async ({ page }) => {
    await page.goto("/tickets/new");
    await page.getByLabel("Subject").fill("Printer on fire");
    await page.locator("#name").fill("Linus T");
    await page.locator("#email").fill("linus@example.com");
    await createButton(page).click();
    await expect(page).toHaveURL(/\/tickets\/t\d+/);
    await expect(page.getByRole("heading", { name: "Printer on fire" })).toBeVisible();
  });

  test("validates the new-ticket form before sending", async ({ page }) => {
    await page.goto("/tickets/new");
    await createButton(page).click();
    await expect(page.getByText("Enter a subject")).toBeVisible();
  });

  test("replies on a ticket", async ({ page }) => {
    await page.goto("/tickets/t1");
    await page.getByLabel("Reply to the customer").fill("We are on it.");
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(page.getByText("We are on it.")).toBeVisible();
  });

  test("resolving moves the ticket out of Open", async ({ page }) => {
    await page.goto("/tickets/t1");
    await page.getByRole("button", { name: /Resolve/ }).click();
    await page.goto("/tickets?status=resolved");
    await expect(page.getByRole("link", { name: /Cannot log in/ })).toBeVisible();
  });

  test("the command palette navigates by keyboard", async ({ page }) => {
    await page.keyboard.press("Control+k");
    const box = page.getByRole("combobox", { name: /command/i });
    await expect(box).toBeFocused();
    await box.fill("agents");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/agents/);
  });
});

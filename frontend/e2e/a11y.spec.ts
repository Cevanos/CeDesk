import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { signedIn } from "./helpers";

/** Let entrance animations finish: mid-fade colours would give false contrast results. */
async function settle(page: Page) {
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
        .map((a) => a.finished.catch(() => undefined)),
    ),
  );
}

async function scan(page: Page) {
  await settle(page);
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const summary = results.violations.map((v) => `${v.id}: ${v.nodes.length} node(s) - ${v.help}`);
  expect(summary, summary.join("\n")).toEqual([]);
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`accessibility (${scheme})`, () => {
    test.use({ colorScheme: scheme });

    test("login page has no WCAG A/AA violations", async ({ page }) => {
      await page.goto("/login");
      await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
      await scan(page);
    });

    test("ticket queue has no WCAG A/AA violations", async ({ page }) => {
      await signedIn(page);
      await expect(page.getByRole("link", { name: /Cannot log in/ })).toBeVisible();
      await scan(page);
    });

    test("ticket detail has no WCAG A/AA violations", async ({ page }) => {
      await signedIn(page);
      await page.goto("/tickets/t1");
      await expect(page.getByRole("heading", { name: "Cannot log in" })).toBeVisible();
      await scan(page);
    });
  });
}

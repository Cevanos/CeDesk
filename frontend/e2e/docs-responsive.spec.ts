import { expect, test, type Page } from "@playwright/test";

/** The public /docs pages must fit every screen, keep a fixed header and have a working mobile menu. */
const VIEWPORTS = [
  { name: "phone-small", width: 320, height: 640 },
  { name: "phone", width: 375, height: 667 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "laptop", width: 1024, height: 768 },
  { name: "desktop", width: 1440, height: 900 },
] as const;

const ROUTES = [
  "/docs",
  "/docs/getting-started",
  "/docs/getting-started/what-cedesk-is",
  "/docs/working-with-tickets",
  "/docs/api",
  "/docs/api/tickets",
  "/docs/developers/quickstart",
  "/docs/glossary",
];

async function offenders(page: Page) {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const scrolls = (el: Element | null) => {
      for (let n = el; n && n !== document.body; n = n.parentElement) {
        const o = getComputedStyle(n).overflowX;
        if (o === "auto" || o === "scroll" || o === "hidden" || o === "clip") return true;
      }
      return false;
    };
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll("body *"))) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.right <= vw + 1 && r.left >= -1) continue;
      if (getComputedStyle(el).position === "fixed") continue;
      if (el.closest("[aria-hidden='true'], .sr-only")) continue;
      if (scrolls(el.parentElement)) continue;
      out.push(
        `${el.tagName.toLowerCase()}.${(el.getAttribute("class") ?? "").slice(0, 60)} [${Math.round(r.left)}→${Math.round(r.right)}]`,
      );
      if (out.length >= 8) break;
    }
    return { scrollWidth: document.documentElement.scrollWidth, vw, out };
  });
}

for (const vp of VIEWPORTS) {
  test(`docs fit ${vp.name} (${vp.width}px)`, async ({ page }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    const problems: string[] = [];
    for (const route of ROUTES) {
      await page.goto(route);
      await page.waitForLoadState("networkidle");
      const { scrollWidth, vw, out } = await offenders(page);
      if (scrollWidth > vw)
        problems.push(`${route}: page scrolls sideways (${scrollWidth} > ${vw})`);
      for (const o of out) problems.push(`${route}: ${o}`);
      // Header must not wrap: it stays one 64px row.
      const h = await page.getByRole("banner").evaluate((el) => el.getBoundingClientRect().height);
      if (h > 70) problems.push(`${route}: header is ${Math.round(h)}px tall (wrapped)`);
    }
    expect(problems, problems.join("\n")).toEqual([]);
  });
}

test("table cells do not break words letter by letter on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  for (const route of ["/docs/api/tickets", "/docs/getting-started/what-cedesk-is"]) {
    await page.goto(route);
    const narrow = await page
      .locator(".docs-prose td")
      .evaluateAll(
        (tds) =>
          tds.filter(
            (t) => t.getBoundingClientRect().width < 56 && (t.textContent ?? "").trim().length > 3,
          ).length,
      );
    expect(narrow, route).toBe(0);
  }
});

test("header stays fixed while scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/docs/api/tickets");
  await page.mouse.wheel(0, 1500);
  await page.waitForTimeout(300);
  const top = await page.getByRole("banner").evaluate((el) => el.getBoundingClientRect().top);
  expect(top).toBe(0);
});

test("mobile menu opens, navigates and closes", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto("/docs");
  await page.getByRole("button", { name: "Open menu" }).click();
  const menu = page.locator("#docs-mobile-menu");
  await expect(menu).toBeVisible();
  const box = await menu.boundingBox();
  expect(box!.height).toBeGreaterThan(400); // fills the screen under the header, not clipped
  await menu.getByRole("link", { name: "Channels" }).click();
  await expect(page).toHaveURL(/\/docs\/channels$/);
  await expect(menu).toBeHidden();
});

test("search dialog fits a phone and finds articles", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 });
  await page.goto("/docs");
  await page.getByRole("button", { name: "Search the documentation" }).first().click();
  const dialog = page.getByRole("dialog");
  await page.keyboard.type("webhook");
  await expect(dialog.getByRole("button").first()).toBeVisible();
  const box = await dialog.locator("> div").boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(321);
  expect(box!.y + box!.height).toBeLessThanOrEqual(569);
});

test("topic dropdown closes after choosing an article", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto("/docs/getting-started");
  const details = page.locator("details").first();
  await details.locator("summary").click();
  await expect(details).toHaveAttribute("open", "");
  await details.getByRole("link", { name: "Create your workspace" }).click();
  await expect(page).toHaveURL(/create-your-workspace$/);
  await expect(page.locator("details").first()).not.toHaveAttribute("open", "");
});

test("touch targets in the mobile sidebar are at least 44px", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto("/docs/getting-started");
  await page.locator("details summary").first().click();
  const heights = await page
    .locator("details a")
    .evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().height)));
  expect(Math.min(...heights)).toBeGreaterThanOrEqual(44);
});

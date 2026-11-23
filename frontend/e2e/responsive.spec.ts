import { expect, test, type Page } from "@playwright/test";

import { resetBackend, signedIn } from "./helpers";

/**
 * Responsive audit: every key screen must fit the viewport on phones, tablets and
 * laptops. Two checks per screen/width:
 *   1. the page itself never scrolls sideways;
 *   2. no element pokes past the viewport edge unless it sits inside a container
 *      that is deliberately scrollable (tables, tab strips, code blocks).
 */
const VIEWPORTS = [
  { name: "phone-small", width: 320, height: 640 },
  { name: "phone", width: 375, height: 667 },
  { name: "phone-large", width: 430, height: 932 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "laptop", width: 1024, height: 768 },
  { name: "desktop", width: 1440, height: 900 },
] as const;

const ROUTES = [
  "/tickets",
  "/tickets/new",
  "/tickets/t1",
  "/agents",
  "/archive",
  "/reports/team",
  "/account",
  "/settings/members",
  "/settings/sla",
  "/settings/imports",
  "/settings/help-center",
  "/settings/security",
];

async function overflowOffenders(page: Page) {
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
      const cs = getComputedStyle(el);
      if (cs.position === "fixed" || cs.visibility === "hidden") continue;
      if (el.closest("[aria-hidden='true'], .sr-only")) continue;
      if (scrolls(el.parentElement)) continue;
      const cls = (el.getAttribute("class") ?? "").slice(0, 70);
      out.push(`${el.tagName.toLowerCase()}.${cls} [${Math.round(r.left)}→${Math.round(r.right)}]`);
      if (out.length >= 8) break;
    }
    return { scrollWidth: document.documentElement.scrollWidth, vw, out };
  });
}

test.describe("responsive layout", () => {
  test.beforeEach(async ({ page }) => {
    await resetBackend();
    await signedIn(page);
  });

  for (const vp of VIEWPORTS) {
    test(`fits ${vp.name} (${vp.width}px)`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      const problems: string[] = [];
      for (const route of ROUTES) {
        await page.goto(route);
        await page.waitForLoadState("networkidle");
        const { scrollWidth, vw, out } = await overflowOffenders(page);
        if (scrollWidth > vw)
          problems.push(`${route}: page scrolls sideways (${scrollWidth} > ${vw})`);
        for (const o of out) problems.push(`${route}: ${o}`);
      }
      expect(problems, problems.join("\n")).toEqual([]);
    });
  }

  test("header popovers stay on screen on a small phone", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    await page.goto("/tickets");
    for (const name of [/notifications/i, /account|user|menu/i]) {
      const trigger = page.getByRole("banner").getByRole("button", { name }).last();
      if (!(await trigger.isVisible())) continue;
      await trigger.click();
      const menu = page.getByRole("menu").first();
      if (await menu.isVisible()) {
        const box = await menu.boundingBox();
        expect(box?.x ?? 0).toBeGreaterThanOrEqual(0);
        expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(320 + 1);
      }
      await page.keyboard.press("Escape");
    }
  });
});

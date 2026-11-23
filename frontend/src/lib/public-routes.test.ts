import { describe, expect, it } from "vitest";

import { isPublicPage } from "@/lib/public-routes";

describe("isPublicPage", () => {
  it.each([
    "/rate",
    "/unsubscribe",
    "/sso/callback",
    "/acme-store/tickets/9b2f-1",
    "/acme-store/tickets/9b2f-1/",
    "/acme-store/portal",
    "/acme-store/contact",
    "/acme-store/help",
    "/acme-store/help/a1",
  ])("opens %s without the agent login", (p) => expect(isPublicPage(p)).toBe(true));

  it.each([
    "/",
    "/login",
    "/signup",
    "/sso/other",
    "/tickets",
    "/tickets/abc",
    "/tickets/new",
    "/settings/sla",
    "/settings/portal",
    "/customers/x",
    "/agents",
    "/campaigns",
    "/campaigns/suppressions",
    "/account",
    "/archive/tickets/abc",
    "/reports/team",
    "/acme-store",
    "/acme-store/tickets",
    "/acme-store/tickets/a/b",
    "/acme-store/settings",
  ])("keeps %s behind the agent login", (p) => expect(isPublicPage(p)).toBe(false));
});

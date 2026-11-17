import { describe, expect, it } from "vitest";

import { ALLOWED_AREAS, isPortalPath, isPublicPath, isRateLimitedPath } from "@/lib/server/config";

describe("proxy path classes", () => {
  it("treats the public area and portal sign-in as token-free", () => {
    expect(isPublicPath("auth/sign-in")).toBe(true);
    expect(isPublicPath("public/csat/tok")).toBe(true);
    expect(isPublicPath("public/acme/articles/a1/vote")).toBe(true);
    expect(isPublicPath("portal/acme/code")).toBe(true);
    expect(isPublicPath("portal/acme/session")).toBe(true);
  });

  it("keeps every other portal and agent route behind a token", () => {
    expect(isPublicPath("portal/tickets")).toBe(false);
    expect(isPublicPath("portal/sign-out")).toBe(false);
    expect(isPublicPath("portal/acme/other")).toBe(false);
    expect(isPublicPath("tickets")).toBe(false);
    expect(isPublicPath("publicity/x")).toBe(false);
  });

  it("knows the portal area", () => {
    expect(isPortalPath("portal/tickets/1")).toBe(true);
    expect(isPortalPath("portals")).toBe(false);
    expect(isPortalPath("tickets/portal/1")).toBe(false);
  });

  it("rate limits credential calls including the portal code and session", () => {
    expect(isRateLimitedPath("auth/sign-in")).toBe(true);
    expect(isRateLimitedPath("portal/acme/code")).toBe(true);
    expect(isRateLimitedPath("portal/acme/session")).toBe(true);
    expect(isRateLimitedPath("portal/tickets")).toBe(false);
  });

  it("allows the Phase 2 areas", () => {
    for (const a of ["portal", "public", "reports", "holidays", "articles", "triage-settings"])
      expect(ALLOWED_AREAS.has(a)).toBe(true);
  });

  it("allows the Phase 3 admin and developer areas the Settings screens call", () => {
    for (const a of [
      "roles",
      "apps",
      "api-keys",
      "webhooks",
      "audit",
      "privacy",
      "retention",
      "usage",
      "security",
      "security-settings",
      "sso-settings",
      "demo",
      "time",
      "side-conversations",
    ])
      expect(ALLOWED_AREAS.has(a)).toBe(true);
  });

  it("still refuses an area the app does not use", () => {
    expect(ALLOWED_AREAS.has("internal")).toBe(false);
    expect(ALLOWED_AREAS.has("admin")).toBe(false);
  });
});

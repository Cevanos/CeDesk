// @vitest-environment node
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { proxy } from "@/proxy";

const req = (path: string, cookie?: string) =>
  new NextRequest(`http://localhost:3000${path}`, {
    headers: cookie ? { cookie } : undefined,
  });

describe("route guard", () => {
  it("sends signed-out visitors to login and remembers where they were going", () => {
    const res = proxy(req("/tickets/42?tab=files"));
    expect(res.status).toBe(307);
    const loc = new URL(res.headers.get("location") ?? "");
    expect(loc.pathname).toBe("/login");
    expect(loc.searchParams.get("next")).toBe("/tickets/42?tab=files");
  });

  it("does not add ?next for the bare root", () => {
    const loc = new URL(proxy(req("/")).headers.get("location") ?? "");
    expect(loc.search).toBe("");
  });

  it("lets signed-out visitors reach auth pages", () => {
    expect(proxy(req("/login")).status).toBe(200);
    expect(proxy(req("/forgot-password")).status).toBe(200);
  });

  it("bounces signed-in users away from login", () => {
    const res = proxy(req("/login", "cdesk_session=t"));
    expect(new URL(res.headers.get("location") ?? "").pathname).toBe("/tickets");
  });

  it("sends a signed-in user to the page they were headed for", () => {
    const res = proxy(req("/login?next=%2Ftickets%2F42", "cdesk_session=t"));
    expect(new URL(res.headers.get("location") ?? "").pathname).toBe("/tickets/42");
  });

  it("ignores an unsafe next target", () => {
    const res = proxy(req("/login?next=https%3A%2F%2Fevil.test", "cdesk_session=t"));
    expect(new URL(res.headers.get("location") ?? "").host).toBe("localhost:3000");
  });

  it("keeps reset links working while signed in", () => {
    expect(proxy(req("/reset-password?token=x", "cdesk_session=t")).status).toBe(200);
  });
});

describe("content security policy", () => {
  it("sets a nonce-based CSP on every response", () => {
    for (const res of [proxy(req("/tickets", "cdesk_session=t")), proxy(req("/tickets"))]) {
      const csp = res.headers.get("content-security-policy") ?? "";
      expect(csp).toMatch(/script-src 'self' 'nonce-[^']+' 'strict-dynamic'/);
    }
  });

  it("uses a different nonce each request", () => {
    const nonce = (r: Response) =>
      /'nonce-([^']+)'/.exec(r.headers.get("content-security-policy") ?? "")?.[1];
    const a = nonce(proxy(req("/tickets", "cdesk_session=t")));
    const b = nonce(proxy(req("/tickets", "cdesk_session=t")));
    expect(a).toBeTruthy();
    expect(a).not.toBe(b);
  });
});

import { describe, expect, it } from "vitest";

import { buildCsp, makeNonce } from "@/lib/csp";

describe("buildCsp", () => {
  const prod = buildCsp("abc", false);

  it("allows scripts only by nonce, never unsafe-inline", () => {
    expect(prod).toContain("script-src 'self' 'nonce-abc' 'strict-dynamic'");
    expect(prod).not.toMatch(/script-src[^;]*unsafe-inline/);
    expect(prod).not.toContain("unsafe-eval");
  });

  it("pins the browser to this origin for network calls", () => {
    expect(prod).toContain("connect-src 'self'");
    expect(prod).toContain("frame-ancestors 'none'");
    expect(prod).toContain("upgrade-insecure-requests");
  });

  it("permits eval only in development", () => {
    const dev = buildCsp("abc", true);
    expect(dev).toContain("'unsafe-eval'");
    expect(dev).not.toContain("upgrade-insecure-requests");
  });
});

describe("buildCsp on the sign-up page", () => {
  const on = buildCsp("abc", false, { turnstile: true });
  const off = buildCsp("abc", false);

  it("allows Cloudflare Turnstile for scripts and frames", () => {
    expect(on).toMatch(/script-src[^;]*https:\/\/challenges\.cloudflare\.com/);
    expect(on).toContain("frame-src https://challenges.cloudflare.com");
    expect(on).toContain("'nonce-abc'");
  });

  it("keeps every other page strict", () => {
    expect(off).not.toContain("cloudflare");
    expect(off).toContain("frame-src 'none'");
  });
});

describe("makeNonce", () => {
  it("is unique per call", () => expect(makeNonce()).not.toBe(makeNonce()));
});

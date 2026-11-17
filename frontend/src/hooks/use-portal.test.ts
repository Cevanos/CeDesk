import { beforeEach, describe, expect, it, vi } from "vitest";

import { visitorId } from "@/hooks/use-portal";

describe("visitorId", () => {
  beforeEach(() => localStorage.clear());

  it("makes an id the server accepts (8 to 100 characters) and keeps it", () => {
    const first = visitorId();
    expect(first.length).toBeGreaterThanOrEqual(8);
    expect(first.length).toBeLessThanOrEqual(100);
    expect(visitorId()).toBe(first);
  });

  it("replaces a stored value that is too short", () => {
    localStorage.setItem("cdesk.visitor", "abc");
    expect(visitorId().length).toBeGreaterThanOrEqual(8);
  });

  it("works on a page where crypto.randomUUID does not exist (plain http)", () => {
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
    expect(visitorId()).toMatch(/^[0-9a-f-]{36}$/);
    vi.unstubAllGlobals();
  });
});

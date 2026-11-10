import { describe, expect, it } from "vitest";

import { createLimiter } from "@/lib/server/rate-limit";

describe("createLimiter", () => {
  it("allows up to the limit then blocks with a retry hint", () => {
    const l = createLimiter(3, 60_000);
    expect([1, 2, 3].map(() => l.hit("a", 0).ok)).toEqual([true, true, true]);
    expect(l.hit("a", 10_000)).toEqual({ ok: false, retryAfter: 50 });
  });

  it("tracks keys independently", () => {
    const l = createLimiter(1, 1000);
    expect(l.hit("a", 0).ok).toBe(true);
    expect(l.hit("b", 0).ok).toBe(true);
    expect(l.hit("a", 1).ok).toBe(false);
  });

  it("opens a new window after it expires", () => {
    const l = createLimiter(1, 1000);
    l.hit("a", 0);
    expect(l.hit("a", 500).ok).toBe(false);
    expect(l.hit("a", 1000).ok).toBe(true);
  });

  it("bounds memory under a flood of unique keys", () => {
    const l = createLimiter(1, 60_000, 5);
    for (let i = 0; i < 50; i++) l.hit(`k${i}`, 0);
    expect(l.hit("fresh", 1).ok).toBe(true);
  });

  it("can be reset", () => {
    const l = createLimiter(1, 1000);
    l.hit("a", 0);
    l.reset();
    expect(l.hit("a", 1).ok).toBe(true);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { cn, initials, safeNext, timeAgo } from "@/lib/utils";

describe("safeNext", () => {
  it.each([
    ["/tickets/5", "/tickets/5"],
    ["/tickets?status=open", "/tickets?status=open"],
  ])("keeps same-origin path %s", (input, out) => expect(safeNext(input)).toBe(out));

  it.each([
    "https://evil.example",
    "//evil.example",
    "/\\evil.example",
    "javascript:alert(1)",
    // Browsers drop tabs and newlines, which would turn these into //evil.example.
    "/\t/evil.example",
    "/\n/evil.example",
    "/\r/evil.example",
    "",
    null,
    undefined,
  ])("rejects %s", (input) => expect(safeNext(input)).toBe("/tickets"));

  it("uses a custom fallback", () => expect(safeNext("//x", "/home")).toBe("/home"));
});

describe("initials", () => {
  it("takes up to two initials", () => expect(initials("ada king lovelace")).toBe("AK"));
  it("handles empty input", () => {
    expect(initials(null)).toBe("?");
    expect(initials("")).toBe("?");
  });
});

describe("cn", () => {
  it("merges conflicting tailwind classes, last wins", () =>
    expect(cn("px-2", "px-4", false && "hidden")).toBe("px-4"));
});

describe("timeAgo", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-15T12:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  it.each([
    ["2026-06-15T11:59:40Z", "just now"],
    ["2026-06-15T11:45:00Z", "15m ago"],
    ["2026-06-15T09:00:00Z", "3h ago"],
    ["2026-06-12T12:00:00Z", "3d ago"],
  ])("%s -> %s", (iso, out) => expect(timeAgo(iso)).toBe(out));

  it("is empty for missing input", () => expect(timeAgo(null)).toBe(""));
});

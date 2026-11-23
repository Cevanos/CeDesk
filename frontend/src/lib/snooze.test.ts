import { describe, expect, it } from "vitest";

import { localInputToIso, localInputValue, snoozePresets } from "@/lib/snooze";

describe("snoozePresets", () => {
  it("offers later today, tomorrow 9:00 and next Monday 9:00", () => {
    const now = new Date(2026, 10, 19, 14, 30); // Thu 19 Nov 2026, local
    const [later, tomorrow, monday] = snoozePresets(now);
    expect(new Date(later!.until).getTime() - now.getTime()).toBe(3 * 3600_000);
    const t = new Date(tomorrow!.until);
    expect([t.getDate(), t.getHours(), t.getMinutes()]).toEqual([20, 9, 0]);
    const m = new Date(monday!.until);
    expect([m.getDay(), m.getDate(), m.getHours()]).toEqual([1, 23, 9]);
  });

  it("on a Monday, next Monday is a week away", () => {
    const now = new Date(2026, 10, 23, 8, 0);
    const m = new Date(snoozePresets(now)[2]!.until);
    expect(m.getDate()).toBe(30);
  });

  it("sends every time with a zone", () => {
    for (const p of snoozePresets()) expect(p.until).toMatch(/Z$/);
  });
});

describe("datetime-local helpers", () => {
  it("round-trips a local time", () => {
    const d = new Date(2026, 10, 21, 9, 5);
    const v = localInputValue(d);
    expect(v).toBe("2026-11-21T09:05");
    expect(localInputToIso(v)).toBe(d.toISOString());
  });
  it("returns null for empty or junk", () => {
    expect(localInputToIso("")).toBeNull();
    expect(localInputToIso("nope")).toBeNull();
  });
});

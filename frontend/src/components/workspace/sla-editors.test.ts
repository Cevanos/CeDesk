import { describe, expect, it } from "vitest";

import { conditionKindsFor } from "@/components/workspace/item-builder";
import { sortHolidays } from "@/components/workspace/sla-holidays";
import { hoursError, timeZones } from "@/components/workspace/sla-hours-editor";
import { reorderPolicies, targetError } from "@/components/workspace/sla-policies-editor";

describe("reorderPolicies", () => {
  const rows = ["a", "b", "c", "fallback"];
  it("moves a row", () => expect(reorderPolicies(rows, 0, 2)).toEqual(["b", "c", "a", "fallback"]));
  it("never moves the last row", () => expect(reorderPolicies(rows, 3, 0)).toBe(rows));
  it("never moves a row past the last one", () => expect(reorderPolicies(rows, 1, 3)).toBe(rows));
});

describe("targetError", () => {
  it("accepts empty targets", () =>
    expect(targetError({ first: "", resolve: "" })).toBeUndefined());
  it("needs whole minutes", () =>
    expect(targetError({ first: "1.5", resolve: "" })).toMatch(/whole/));
  it("keeps first reply before resolution", () =>
    expect(targetError({ first: "90", resolve: "60" })).toMatch(/later/));
});

describe("business hours", () => {
  it("needs closing after opening", () => {
    expect(hoursError("09:00", "18:00")).toBeUndefined();
    expect(hoursError("18:00", "09:00")).toMatch(/after/);
    expect(hoursError("", "09:00")).toMatch(/both/);
  });
  it("always lists the current zone", () =>
    expect(timeZones("Mars/Olympus")[0]).toBe("Mars/Olympus"));
});

describe("sortHolidays", () => {
  it("sorts by date and flags past days", () => {
    const out = sortHolidays(
      [
        { id: "2", day: "2026-12-25", name: "Christmas" },
        { id: "1", day: "2026-01-26", name: "Republic Day" },
      ],
      "2026-11-12",
    );
    expect(out.map((h) => h.id)).toEqual(["1", "2"]);
    expect(out.map((h) => h.past)).toEqual([true, false]);
  });
});

describe("conditionKindsFor", () => {
  it("hides `SLA is` on a new ticket", () => {
    expect(conditionKindsFor("created").some((k) => k.kind === "sla_in")).toBe(false);
  });
  it.each(["customer_replied", "sla_breached"])("offers `SLA is` on %s", (event) => {
    expect(conditionKindsFor(event).some((k) => k.kind === "sla_in")).toBe(true);
  });
});

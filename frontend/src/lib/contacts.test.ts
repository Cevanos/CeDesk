import { describe, expect, it } from "vitest";

import { qs } from "@/lib/api/client";
import {
  describeFilters,
  EMPTY_FILTERS,
  filtersToQuery,
  filtersToSegment,
  isFiltered,
  keyFromLabel,
  segmentToFilters,
  showValue,
} from "@/lib/contacts";

const fields = [
  { key: "plan", label: "Plan", kind: "select" as const, options: ["free", "pro"] },
  { key: "seats", label: "Seats", kind: "number" as const, options: null },
  { key: "trial", label: "Trial", kind: "checkbox" as const, options: null },
];

describe("contact filters", () => {
  it("repeats attr and leaves out what is not set", () => {
    const f = { ...EMPTY_FILTERS, vip: true, attributes: { plan: "pro", trial: "true" } };
    expect(qs(filtersToQuery(f))).toBe("?vip=true&attr=plan%3Apro&attr=trial%3Atrue");
    expect(qs(filtersToQuery(EMPTY_FILTERS))).toBe("");
  });

  it("does not count sort as a filter", () => {
    expect(isFiltered({ ...EMPTY_FILTERS, sort: "recent" })).toBe(false);
    expect(isFiltered({ ...EMPTY_FILTERS, q: " ana " })).toBe(true);
  });

  it("makes a segment from the list and back", () => {
    const f = {
      ...EMPTY_FILTERS,
      has_open: true,
      sort: "recent" as const,
      attributes: { plan: "pro", seats: "12", trial: "true" },
    };
    const s = filtersToSegment(f, fields);
    expect(s).toEqual({
      has_open: true,
      sort: "recent",
      attributes: { plan: "pro", seats: 12, trial: true },
    });
    expect(segmentToFilters(s)).toEqual(f);
    expect(describeFilters(s, fields)).toEqual([
      "Has open tickets",
      "Plan: pro",
      "Seats: 12",
      "Trial: true",
    ]);
  });
});

describe("helpers", () => {
  it("suggests a key from a label", () => {
    expect(keyFromLabel("Plan tier")).toBe("plan_tier");
    expect(keyFromLabel("  2nd site! ")).toBe("f_2nd_site");
    expect(keyFromLabel("!!!")).toBe("");
  });

  it("shows empty values as a dash", () => {
    expect(showValue(null)).toBe("—");
    expect(showValue("")).toBe("—");
    expect(showValue(false)).toBe("No");
    expect(showValue(12)).toBe("12");
  });
});

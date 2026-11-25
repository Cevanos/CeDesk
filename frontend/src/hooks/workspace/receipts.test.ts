import { describe, expect, it } from "vitest";

import { receiptRequired } from "@/hooks/workspace/receipts";

describe("receiptRequired", () => {
  it("is never required when off or optional", () => {
    expect(receiptRequired({ mode: "off", required_teams: [] }, "t1")).toBe(false);
    expect(receiptRequired({ mode: "optional", required_teams: ["t1"] }, "t1")).toBe(false);
  });
  it("applies to every team when none are listed", () => {
    expect(receiptRequired({ mode: "required", required_teams: [] }, null)).toBe(true);
  });
  it("applies only to the listed teams", () => {
    const s = { mode: "required", required_teams: ["t1"] } as const;
    expect(receiptRequired(s, "t1")).toBe(true);
    expect(receiptRequired(s, "t2")).toBe(false);
    expect(receiptRequired(s, null)).toBe(false);
  });
});

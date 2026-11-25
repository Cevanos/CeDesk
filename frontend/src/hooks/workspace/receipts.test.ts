import { describe, expect, it } from "vitest";

import { receiptRequired } from "@/hooks/workspace/receipts";
import type { ReceiptSettings } from "@/lib/api/differentiator-types";

describe("receiptRequired", () => {
  it("is never required when off or optional", () => {
    expect(receiptRequired({ mode: "off", required_teams: [] }, "t1")).toBe(false);
    expect(receiptRequired({ mode: "optional", required_teams: ["t1"] }, "t1")).toBe(false);
  });
  it("applies to every team when none are listed", () => {
    expect(receiptRequired({ mode: "required", required_teams: [] }, null)).toBe(true);
  });
  it("applies only to the listed teams", () => {
    const s: ReceiptSettings = { mode: "required", required_teams: ["t1"] };
    expect(receiptRequired(s, "t1")).toBe(true);
    expect(receiptRequired(s, "t2")).toBe(false);
    expect(receiptRequired(s, null)).toBe(false);
  });
});

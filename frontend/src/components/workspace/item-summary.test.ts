import { describe, expect, it, vi } from "vitest";

import { AUTOMATION_CONDITION_KINDS, summarize } from "@/components/workspace/item-builder";

// The date picker pulls in a calendar library this test does not need.
vi.mock("@/components/ui/date-picker", () => ({ DatePicker: () => null }));

const sum = (it: Record<string, unknown>, fields?: { key: string; label: string }[]) =>
  summarize(it as never, AUTOMATION_CONDITION_KINDS, fields);

describe("summarize", () => {
  it("reads a time-since condition as a sentence, not raw values", () => {
    expect(sum({ kind: "time_since", of: "customer_reply", op: "more_than", minutes: 60 })).toBe(
      "Time since the last customer reply is more than 1 hour",
    );
  });

  it("names the contact field, the operator and the value", () => {
    const fields = [{ key: "qa_tier", label: "QA Tier" }];
    expect(
      sum({ kind: "contact_field", key: "qa_tier", op: "equals", value: "Gold" }, fields),
    ).toBe("QA Tier equals Gold");
    expect(sum({ kind: "contact_field", key: "qa_tier", op: "is_set", value: "" }, fields)).toBe(
      "QA Tier is set",
    );
  });

  it("falls back to the key while the field names load, and to the label when empty", () => {
    expect(sum({ kind: "contact_field", key: "qa_tier", op: "equals", value: "Gold" })).toBe(
      "qa_tier equals Gold",
    );
    expect(sum({ kind: "contact_field", key: "", op: "equals", value: "" })).toBe("Contact field");
  });
});

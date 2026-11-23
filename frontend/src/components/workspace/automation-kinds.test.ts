import { describe, expect, it } from "vitest";

import { opsForKind } from "@/components/workspace/automation-fields";
import {
  AUTOMATION_ACTION_KINDS,
  automationActionKinds,
  automationConditionKinds,
  summarize,
} from "@/components/workspace/item-builder";

describe("automation kinds", () => {
  it("hides calling a webhook from anyone without manage_access", () => {
    expect(automationActionKinds(false).some((k) => k.kind === "call_webhook")).toBe(false);
    expect(automationActionKinds(true).some((k) => k.kind === "call_webhook")).toBe(true);
  });

  it("offers every Phase 5 condition and action", () => {
    const conditions = automationConditionKinds("customer_replied").map((k) => k.kind);
    for (const k of [
      "contact_field",
      "contact_vip",
      "contact_in_segment",
      "time_since",
      "snoozed",
      "has_followers",
    ])
      expect(conditions).toContain(k);
    const actions = AUTOMATION_ACTION_KINDS.map((k) => k.kind);
    for (const k of ["snooze", "add_follower", "send_email", "call_webhook"])
      expect(actions).toContain(k);
  });

  it("leaves out `SLA is` on a new ticket", () => {
    expect(automationConditionKinds("created").some((k) => k.kind === "sla_in")).toBe(false);
    expect(automationConditionKinds("sla_breached").some((k) => k.kind === "sla_in")).toBe(true);
  });

  it("only allows text operators on text fields and ordering on numbers and dates", () => {
    expect(opsForKind("text")).toContain("contains");
    expect(opsForKind("text")).not.toContain("more_than");
    expect(opsForKind("number")).toContain("more_than");
    expect(opsForKind("date")).toContain("less_than");
    expect(opsForKind("number")).not.toContain("contains");
    expect(opsForKind("checkbox")).toEqual(["equals", "not_equals", "is_set", "is_not_set"]);
  });

  it("summarises a duration in days or hours", () => {
    expect(summarize({ kind: "snooze", minutes: 2880 }, AUTOMATION_ACTION_KINDS)).toBe(
      "Snooze for: 2 days",
    );
  });
});

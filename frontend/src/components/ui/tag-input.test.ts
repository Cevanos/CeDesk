import { describe, expect, it } from "vitest";

import { addTags, normalizeTag } from "@/components/ui/tag-input";

describe("normalizeTag", () => {
  it("trims, lowercases and collapses spaces", () =>
    expect(normalizeTag("  Charge  Back ")).toBe("charge back"));
});

describe("addTags", () => {
  it("splits on commas and drops empties and duplicates", () => {
    const r = addTags(["refund"], "Refund, chargeback, , Chargeback");
    expect(r.tags).toEqual(["refund", "chargeback"]);
    expect(r.error).toBeNull();
  });
  it("keeps the same array when nothing was added", () => {
    const tags = ["a"];
    expect(addTags(tags, " A ").tags).toBe(tags);
  });
  it("rejects what the validator rejects and reports it", () => {
    const r = addTags([], "ok.com, nope", (t) => (t.includes(".") ? null : "Not a domain"));
    expect(r.tags).toEqual(["ok.com"]);
    expect(r.error).toBe("Not a domain");
  });
});

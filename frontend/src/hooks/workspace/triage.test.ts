import { describe, expect, it } from "vitest";

import { resetBody, triageBody } from "@/hooks/workspace/triage";

describe("triage bodies", () => {
  it("a reset sends topics: null and keeps the current redaction switch", () => {
    expect(resetBody({ redact_cards: true })).toEqual({ topics: null, redact_cards: true });
    expect(resetBody({ redact_cards: false })).toEqual({ topics: null, redact_cards: false });
  });

  it("a save sends the topics and the switch, never a reset flag", () => {
    const body = triageBody({ topics: { refund: ["refund"] }, redact_cards: false, shape: "map" });
    expect(body).toEqual({ topics: { refund: ["refund"] }, redact_cards: false });
    expect(body).not.toHaveProperty("reset");
  });
});

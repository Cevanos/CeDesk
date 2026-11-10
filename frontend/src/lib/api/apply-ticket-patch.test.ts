import { describe, expect, it } from "vitest";

import { applyTicketPatch } from "@/lib/api/apply-ticket-patch";
import { makeTicket } from "@/test/fixtures";

describe("applyTicketPatch", () => {
  it("applies scalar fields without mutating the input", () => {
    const t = makeTicket();
    const out = applyTicketPatch(t, { status: "pending", priority: "low", assignee_id: "m1" });
    expect(out).toMatchObject({ status: "pending", priority: "low", assignee_id: "m1" });
    expect(t.status).toBe("open");
  });

  it("can clear an assignment with null", () => {
    expect(
      applyTicketPatch(makeTicket({ assignee_id: "m1" }), { assignee_id: null }).assignee_id,
    ).toBeNull();
  });

  it("adds and removes tags without duplicates", () => {
    const out = applyTicketPatch(makeTicket({ tags: ["a", "b"] }), {
      add_tags: ["b", "c"],
      remove_tags: ["a"],
    });
    expect(out.tags).toEqual(["b", "c"]);
  });

  it("merges custom fields", () => {
    const out = applyTicketPatch(makeTicket({ fields: { order: "1", n: 2 } }), {
      fields: { n: 3 },
    });
    expect(out.fields).toEqual({ order: "1", n: 3 });
  });

  it("leaves untouched fields alone", () => {
    const t = makeTicket({ subject: "keep" });
    expect(applyTicketPatch(t, { status: "resolved" }).subject).toBe("keep");
  });
});

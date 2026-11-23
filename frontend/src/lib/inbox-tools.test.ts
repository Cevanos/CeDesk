import { describe, expect, it } from "vitest";

import { bulkSummary } from "@/components/tickets/inbox/bulk-bar";
import { sameFilters } from "@/components/tickets/inbox/saved-views";
import { validateAutoClose } from "@/components/workspace/auto-close-view";
import { sortViews } from "@/hooks/workspace/views";
import { ApiError } from "@/lib/api/client";
import { apiMessage } from "@/lib/api/messages";
import { filtersFromParams, filtersToView, viewToPatch } from "@/lib/api/ticket-filters";
import type { Me, SavedView } from "@/lib/api/types";
import { inboxActions } from "@/lib/permissions";

const me = (role: Me["role"], actions?: string[]): Me =>
  ({
    id: "m1",
    name: "N",
    email: "n@x.test",
    role,
    access: actions ? { actions } : undefined,
  }) as Me;

describe("inboxActions", () => {
  it("follows GET /me access.actions", () => {
    expect(inboxActions(me("agent", ["bulk_edit"]))).toEqual({
      bulkEdit: true,
      shareViews: false,
      sendTranscript: false,
    });
    expect(inboxActions(me("admin", ["share_views", "send_transcript"]))).toMatchObject({
      bulkEdit: false,
      shareViews: true,
      sendTranscript: true,
    });
  });

  it("falls back to the role matrix when access is missing", () => {
    expect(inboxActions(me("light_agent"))).toEqual({
      bulkEdit: false,
      shareViews: false,
      sendTranscript: false,
    });
    expect(inboxActions(me("agent"))).toEqual({
      bulkEdit: true,
      shareViews: false,
      sendTranscript: true,
    });
    expect(inboxActions(me("supervisor")).shareViews).toBe(true);
  });

  it("grants nothing before the user is known", () => {
    expect(inboxActions(undefined)).toEqual({
      bulkEdit: false,
      shareViews: false,
      sendTranscript: false,
    });
  });
});

describe("saved view filters", () => {
  it("saves only set values and drops the defaults", () => {
    const f = filtersFromParams(new URLSearchParams("status=pending&scope=mine&sort=priority"));
    expect(filtersToView(f)).toEqual({ status: "pending", scope: "mine" });
  });

  it("keeps snoozed and search text", () => {
    const f = filtersFromParams(new URLSearchParams("status=pending&snoozed=true&q=refund"));
    expect(filtersToView(f)).toEqual({ status: "pending", snoozed: "true", q: "refund" });
  });

  it("applies a view by setting its keys and clearing the others", () => {
    expect(viewToPatch({ status: "pending", team: "t1" })).toEqual({
      status: "pending",
      scope: null,
      priority: null,
      team: "t1",
      q: null,
      sort: null,
      imported: null,
      snoozed: null,
    });
  });

  it("treats the open tab as the default in the URL", () => {
    expect(viewToPatch({ status: "open" }).status).toBeNull();
  });

  it("ignores an unknown snoozed value in the URL", () => {
    expect(filtersFromParams(new URLSearchParams("snoozed=maybe")).snoozed).toBe("");
  });

  it("matches a view to the list, with a missing status meaning open", () => {
    expect(sameFilters({ scope: "mine" }, { status: "open", scope: "mine" })).toBe(true);
    expect(sameFilters({ status: "pending" }, { status: "open" })).toBe(false);
    expect(sameFilters({ status: "open", q: "a" }, { status: "open" })).toBe(false);
  });

  it("lists shared views first, then by name", () => {
    const v = (name: string, shared: boolean) => ({ id: name, name, shared }) as SavedView;
    expect(
      sortViews([v("b", false), v("z", true), v("a", false), v("c", true)]).map((x) => x.name),
    ).toEqual(["c", "z", "a", "b"]);
  });
});

describe("bulkSummary", () => {
  it("reports all good and partial results", () => {
    expect(bulkSummary({ succeeded: 50, failed: 0 })).toBe("Changed 50 tickets");
    expect(bulkSummary({ succeeded: 1, failed: 0 })).toBe("Changed 1 ticket");
    expect(bulkSummary({ succeeded: 47, failed: 3 })).toBe("Changed 47 of 50 tickets");
  });
});

describe("validateAutoClose", () => {
  it("accepts 1 to 365 whole days", () => {
    expect(validateAutoClose("1", "")).toEqual({});
    expect(validateAutoClose("365", "")).toEqual({});
  });
  it("refuses days out of range or not whole", () => {
    for (const d of ["0", "366", "2.5", "", "abc"]) {
      expect(validateAutoClose(d, "").after_days).toBeTruthy();
    }
  });
  it("refuses a notice over 5000 characters", () => {
    expect(validateAutoClose("7", "x".repeat(5000))).toEqual({});
    expect(validateAutoClose("7", "x".repeat(5001)).notice).toBeTruthy();
  });
});

describe("apiMessage", () => {
  const err = (
    status: number,
    message: string,
    extra: Partial<ConstructorParameters<typeof ApiError>[0]> = {},
  ) => new ApiError({ status, code: "X", message, ...extra });

  it("shows the server message as it is", () => {
    expect(apiMessage(err(409, "A resolved ticket can't be snoozed. Reopen it first."))).toBe(
      "A resolved ticket can't be snoozed. Reopen it first.",
    );
  });
  it("prefers 422 field messages", () => {
    expect(
      apiMessage(err(422, "Invalid", { details: [{ field: "until", message: "Too soon." }] })),
    ).toBe("Too soon.");
  });
  it("says how long to wait on a rate limit", () => {
    expect(apiMessage(err(429, "slow", { retryAfter: 12 }))).toBe(
      "Too many requests, try again in 12 seconds.",
    );
  });
  it("uses the fallback for non-API errors", () => {
    expect(apiMessage(new Error("boom"), "Nope")).toBe("Nope");
  });
});

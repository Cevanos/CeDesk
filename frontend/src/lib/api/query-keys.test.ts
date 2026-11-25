import { describe, expect, it } from "vitest";

import { queryKeys } from "@/lib/api/query-keys";

/** TanStack Query invalidates by prefix, so a broad key must be a prefix of its children. */
function isPrefix(prefix: readonly unknown[], key: readonly unknown[]) {
  return prefix.every((part, i) => JSON.stringify(part) === JSON.stringify(key[i]));
}

describe("queryKeys", () => {
  it("keeps child keys under their broad invalidation prefix", () => {
    expect(isPrefix(queryKeys.tickets.all, queryKeys.tickets.lists)).toBe(true);
    expect(isPrefix(queryKeys.tickets.lists, queryKeys.tickets.list({ status: "open" }))).toBe(
      true,
    );
    expect(isPrefix(queryKeys.tickets.all, queryKeys.tickets.detail("t1"))).toBe(true);
    expect(isPrefix(queryKeys.savedReplies.all, queryKeys.savedReplies.search("x"))).toBe(true);
    expect(isPrefix(queryKeys.notifications.all, queryKeys.notifications.list(true))).toBe(true);
    expect(isPrefix(queryKeys.members.all, queryKeys.members.options)).toBe(true);
  });

  it("separates keys that must not invalidate each other", () => {
    expect(queryKeys.portal.ticket("1")).not.toEqual(queryKeys.tickets.detail("1"));
    expect(queryKeys.portal.tickets("a")).not.toEqual(queryKeys.portal.tickets("b"));
    expect(queryKeys.public.article("a", "1", "")).not.toEqual(
      queryKeys.public.article("b", "1", ""),
    );
    expect(queryKeys.notifications.list(true)).not.toEqual(queryKeys.notifications.list(false));
  });

  it("includes parameters so different reports do not share a cache entry", () => {
    expect(queryKeys.reports.team({ from: "a" })).not.toEqual(
      queryKeys.reports.team({ from: "b" }),
    );
  });

  it("has unique root segments per top-level feature", () => {
    const roots = [
      queryKeys.agents.all,
      queryKeys.teams.all,
      queryKeys.macros.all,
      queryKeys.sla.all,
      queryKeys.rules.all,
      queryKeys.cevanos.all,
      queryKeys.ticketTypes.all,
      queryKeys.savedReplies.all,
    ].map((k) => k[0]);
    expect(new Set(roots).size).toBe(roots.length);
  });
});

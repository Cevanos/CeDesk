import { describe, expect, it } from "vitest";

import { normalizeTeamReport } from "@/hooks/workspace/reports";

describe("normalizeTeamReport", () => {
  it("reads the straightforward shape", () => {
    const r = normalizeTeamReport({
      from: "2026-11-01",
      to: "2026-11-07",
      summary: {
        created: 12,
        resolved: 9,
        open: 3,
        first_reply_minutes: 14,
        resolve_minutes: 240,
        sla_met_pct: 91.5,
        csat_average: 4.5,
        csat_count: 6,
      },
      csat: [
        { score: 5, count: 4 },
        { score: 4, count: 2 },
      ],
      teams: [{ team_id: "t1", team_name: "Billing", created: 5, resolved: 4, open: 1 }],
      agents: [{ member_id: "m1", name: "Neha", resolved: 7 }],
    });
    expect(r.summary.created).toBe(12);
    expect(r.summary.sla_met_pct).toBe(91.5);
    expect(r.csat.map((c) => c.count)).toEqual([0, 0, 0, 2, 4]);
    expect(r.teams[0]).toMatchObject({ id: "t1", name: "Billing", resolved: 4 });
    expect(r.agents[0]).toMatchObject({ id: "m1", name: "Neha", resolved: 7, open: null });
    expect(r.from).toBe("2026-11-01");
  });

  it("accepts alternative key names and a csat object", () => {
    const r = normalizeTeamReport({
      period: { from: "a", to: "b" },
      totals: { tickets_created: "8", tickets_resolved: 5, avg_first_reply_minutes: 20 },
      csat: { average: 4, count: 3, distribution: { "5": 1, "3": 2 } },
      by_team: [{ id: "x", name: "Tech", tickets_created: 2 }],
      members: [{ agent_id: "m", agent_name: "Chetan" }],
    });
    expect(r.summary).toMatchObject({
      created: 8,
      resolved: 5,
      first_reply_minutes: 20,
      csat_average: 4,
      csat_count: 3,
    });
    expect(r.csat.map((c) => c.count)).toEqual([0, 0, 2, 0, 1]);
    expect(r.teams[0]).toMatchObject({ name: "Tech", created: 2 });
    expect(r.agents[0]).toMatchObject({ id: "m", name: "Chetan" });
  });

  it("turns a 0-1 ratio into a percentage", () => {
    expect(normalizeTeamReport({ summary: { sla_met_pct: 0.875 } }).summary.sla_met_pct).toBe(87.5);
  });

  it("survives garbage", () => {
    const r = normalizeTeamReport(null);
    expect(r.teams).toEqual([]);
    expect(r.summary.created).toBeNull();
    expect(r.csat).toHaveLength(5);
  });
});

describe("withOffset", () => {
  it("adds the viewer's offset to a plain date", async () => {
    const { withOffset } = await import("@/hooks/workspace/reports");
    expect(withOffset("2026-11-01")).toMatch(/^2026-11-01T00:00:00[+-]\d{2}:\d{2}$/);
    expect(withOffset("2026-11-30", true)).toMatch(/^2026-11-30T23:59:59[+-]\d{2}:\d{2}$/);
  });
});

import { describe, expect, it } from "vitest";

import { normalizeAiReport, normalizeMetricsReport } from "@/hooks/workspace/reports";

describe("normalizeMetricsReport", () => {
  it("reads tiles, open counts, agents and the governance link", () => {
    const r = normalizeMetricsReport({
      summary: {
        open: 150,
        one_touch_percent: 41.2,
        one_touch: 35,
        one_touch_measured: 85,
        reopen_rate_percent: null,
        time_logged_minutes: 0,
        billable_minutes: 765,
      },
      backlog_by_age: [{ bucket: "lt_1h", label: "Under 1 hour", open: 4 }],
      channels: [{ value: "email", label: "email", created: 7, open: 5 }],
      sentiment: [{ value: null, label: "(none)", created: 1, open: 1 }],
      agents: [
        {
          member_id: "m1",
          name: "Ana",
          logged_minutes: 90,
          billable_minutes: 30,
          open_tasks: 2,
          overdue_tasks: 1,
        },
      ],
      governance_url: null,
    });
    expect(r.tiles.map((t) => [t.key, t.value])).toEqual([
      ["open", 150],
      ["one_touch", 41.2],
      ["reopened", null],
      ["time_logged", 0],
      ["billable", 765],
    ]);
    expect(r.tiles[0]).toMatchObject({
      label: "Open now",
      hint: "As of now, not the date range",
    });
    expect(r.tiles[1]!.hint).toBe("35 of 85 resolved");
    expect(r.backlog).toEqual([{ label: "Under 1 hour", created: null, open: 4 }]);
    expect(r.channels).toEqual([{ label: "email", created: 7, open: 5 }]);
    expect(r.sentiment[0]!.label).toBe("(none)");
    expect(r.agents[0]).toMatchObject({ name: "Ana", open_tasks: 2, overdue_tasks: 1 });
    expect(r.governance_url).toBeNull();
  });

  it("survives an empty answer", () => {
    expect(normalizeMetricsReport(null).backlog).toEqual([]);
  });
});

describe("normalizeAiReport", () => {
  it("reads the nested ai object and turns ratios into percents", () => {
    expect(
      normalizeAiReport({
        ai: {
          conversations: 100,
          handed_over: 23,
          handover_rate: 0.233,
          ai_resolved: 70,
          ai_resolved_rate: 0.699,
          ai_csat: 4.3,
          ai_csat_responses: 61,
        },
      }),
    ).toEqual({
      chats: 100,
      handed_over: 23,
      handed_over_percent: 23.3,
      resolved_by_ai: 70,
      resolved_by_ai_percent: 69.9,
      csat_average: 4.3,
      csat_count: 61,
    });
  });

  it("keeps missing numbers as null", () => {
    expect(normalizeAiReport({ ai: { conversations: 0 } })).toMatchObject({
      chats: 0,
      handed_over: null,
      csat_average: null,
    });
  });
});

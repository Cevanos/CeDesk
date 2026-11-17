import { describe, expect, it } from "vitest";

import { normalizeAiReport, normalizeMetricsReport } from "@/hooks/workspace/reports";

describe("normalizeMetricsReport", () => {
  it("reads tiles, distributions, agents and the governance link", () => {
    const r = normalizeMetricsReport({
      summary: { reopened: 3, avg_first_reply_minutes: 12, sla_met_percent: null },
      backlog: [{ bucket: "0-1d", count: 4 }],
      channels: { email: 7, chat: 2 },
      agents: [{ member_id: "m1", name: "Ana", time_minutes: 90, tasks_done: 2 }],
      governance_url: null,
    });
    expect(r.tiles.map((t) => [t.key, t.value, t.unit])).toEqual([
      ["reopened", 3, "count"],
      ["avg_first_reply_minutes", 12, "minutes"],
      ["sla_met_percent", null, "percent"],
    ]);
    expect(r.backlog).toEqual([{ label: "0-1d", count: 4 }]);
    expect(r.channels).toEqual([
      { label: "email", count: 7 },
      { label: "chat", count: 2 },
    ]);
    expect(r.agents[0]).toMatchObject({ name: "Ana", time_minutes: 90, tasks_done: 2 });
    expect(r.governance_url).toBeNull();
  });

  it("survives an empty answer", () => {
    expect(normalizeMetricsReport(null).tiles).toEqual([]);
  });
});

describe("normalizeAiReport", () => {
  it("keeps missing numbers as null", () => {
    expect(normalizeAiReport({ chats: 10, resolved_by_ai: 6 })).toMatchObject({
      chats: 10,
      resolved_by_ai: 6,
      handed_over: null,
      csat_average: null,
    });
  });
});

import { describe, expect, it } from "vitest";

import { retryOnce } from "@/hooks/workspace/reports-explore";
import { ApiError } from "@/lib/api/client";
import {
  allowedGroupBy,
  bucketLabel,
  createLimiter,
  describeSchedule,
  eachDay,
  fillDayGaps,
  formatValue,
  groupLabel,
  normalizeDefinition,
} from "@/lib/reports";

describe("formatValue", () => {
  it("shows null as a dash, never 0", () => {
    expect(formatValue(null, "percent")).toBe("—");
    expect(formatValue(0, "count")).toBe("0");
  });
  it("formats each unit", () => {
    expect(formatValue(90, "minutes")).toBe("1h 30m");
    expect(formatValue(12.5, "percent")).toBe("12.5%");
    expect(formatValue(4.25, "score")).toBe("4.25 / 5");
  });
});

describe("labels", () => {
  it("labels a week by its Monday", () => {
    expect(bucketLabel("2026-11-17", "week")).toBe("Week of 17 Nov");
    expect(bucketLabel("2026-11-01", "month")).toBe("Nov 2026");
  });
  it("capitalises raw priority, channel and sentiment labels", () => {
    expect(groupLabel({ key: "high", label: "high", value: 1, n: 1 }, "priority")).toBe("High");
    expect(groupLabel({ key: null, label: "No team", value: 1, n: 1 }, "team")).toBe("No team");
  });
});

describe("days", () => {
  it("lists every day, both ends included", () => {
    expect(eachDay("2026-11-29", "2026-12-02")).toEqual([
      "2026-11-29",
      "2026-11-30",
      "2026-12-01",
      "2026-12-02",
    ]);
  });
  it("fills missing snapshot days with gaps (null), not 0", () => {
    const g = fillDayGaps([
      { key: "2026-11-22", label: "", value: 5, n: 5 },
      { key: "2026-11-24", label: "", value: 7, n: 7 },
    ]);
    expect(g.map((x) => x.value)).toEqual([5, null, 7]);
  });
});

describe("builder rules", () => {
  it("open_now cannot be grouped by a time bucket", () => {
    const all = ["none", "day", "week", "month", "team"];
    expect(allowedGroupBy("now", all)).toEqual(["none", "team"]);
    expect(allowedGroupBy("backlog", all)).toEqual(["day"]);
  });
  it("normalises a definition to what the server accepts", () => {
    expect(
      normalizeDefinition(
        { metric: "backlog", group_by: "week", filters: { team: "t1", tag: "x" } },
        "backlog",
      ),
    ).toEqual({ metric: "backlog", group_by: "day", filters: { team: "t1" } });
    expect(
      normalizeDefinition(
        {
          metric: "open_now",
          group_by: "day",
          range: { preset: "last_7d" },
          filters: { imported: "any", tag: " " },
        },
        "now",
      ),
    ).toEqual({ metric: "open_now" });
    expect(normalizeDefinition({ metric: "created", group_by: "none" }, "period")).toEqual({
      metric: "created",
    });
  });
});

describe("createLimiter", () => {
  it("never runs more than max at once", async () => {
    const limit = createLimiter(3);
    let active = 0;
    let peak = 0;
    const task = () =>
      limit(async () => {
        active++;
        peak = Math.max(peak, active);
        await new Promise((r) => setTimeout(r, 5));
        active--;
        return 1;
      });
    const out = await Promise.all(Array.from({ length: 10 }, task));
    expect(out).toHaveLength(10);
    expect(peak).toBe(3);
  });
});

describe("retryOnce", () => {
  const busy = () =>
    new ApiError({ status: 429, code: "RateLimited", message: "busy", retryAfter: 5 });
  it("waits Retry-After and tries once more on a 429", async () => {
    let calls = 0;
    const waits: number[] = [];
    const v = await retryOnce(
      async () => {
        if (++calls === 1) throw busy();
        return "ok";
      },
      async (ms) => void waits.push(ms),
    );
    expect(v).toBe("ok");
    expect(waits).toEqual([5000]);
  });
  it("gives up after one retry and passes other errors straight through", async () => {
    await expect(
      retryOnce(
        async () => {
          throw busy();
        },
        async () => undefined,
      ),
    ).rejects.toMatchObject({ status: 429 });
    let calls = 0;
    await expect(
      retryOnce(async () => {
        calls++;
        throw new ApiError({ status: 422, code: "Invalid", message: "x" });
      }),
    ).rejects.toMatchObject({ status: 422 });
    expect(calls).toBe(1);
  });
});

describe("describeSchedule", () => {
  it("uses ISO weekdays (1 = Monday)", () => {
    expect(
      describeSchedule({
        cadence: "weekly",
        weekday: 1,
        day_of_month: null,
        hour: 9,
        timezone: "Asia/Kolkata",
      }),
    ).toBe("Every Monday at 09:00 (Asia/Kolkata)");
    expect(
      describeSchedule({
        cadence: "monthly",
        weekday: null,
        day_of_month: 2,
        hour: 0,
        timezone: "UTC",
      }),
    ).toBe("On the 2nd of each month at 00:00 (UTC)");
  });
});

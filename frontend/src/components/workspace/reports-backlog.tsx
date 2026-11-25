"use client";

import { LineChart as TrendIcon, Table2 } from "lucide-react";

import { Card, EmptyState, Skeleton } from "@/components/ui/primitives";
import { SectionHeader } from "@/components/ui/section";
import { LoadError } from "@/components/workspace/list-shared";
import { GetCsvButton } from "@/components/workspace/report-csv";
import { LineChart, Legend, TimezoneNote } from "@/components/workspace/report-result";
import { useReportFilters } from "@/components/workspace/reports-frame";
import { withOffset } from "@/hooks/workspace/reports";
import { useBacklog } from "@/hooks/workspace/reports-explore";
import { DASH, datePart, eachDay, shortDay } from "@/lib/reports";
import { cn } from "@/lib/utils";

const SERIES = [
  { key: "open", name: "Open", tone: "text-primary" },
  { key: "pending", name: "Pending", tone: "text-warning" },
  { key: "breaching", name: "Breaching", tone: "text-danger" },
] as const;

/** "Backlog" tab: open, pending and breaching tickets at the end of each past day. */
export function BacklogTab() {
  const { params, badRange } = useReportFilters();
  const query = {
    from: withOffset(params.from),
    to: withOffset(params.to, true),
    team_id: params.team_id,
  };
  const q = useBacklog(query, !badRange);
  if (badRange) return null;
  if (q.isPending) return <Skeleton className="h-72 rounded-xl" />;
  if (q.isError) return <LoadError error={q.error} />;
  const d = q.data;

  if (d.first_day === null && d.days.length === 0) {
    return (
      <Card>
        <EmptyState title="No history yet">
          No history yet. The first snapshot is taken after today ends.
        </EmptyState>
      </Card>
    );
  }

  // Only days with a snapshot are in `days`: a missing day is a gap in the line, never a 0.
  const byDay = new Map(d.days.map((r) => [r.day, r]));
  const days = eachDay(datePart(d.from), datePart(d.to));
  const startsBefore = d.first_day !== null && datePart(d.from) < d.first_day;

  return (
    <div className={cn("space-y-10 transition-opacity", q.isPlaceholderData && "opacity-60")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {startsBefore && d.first_day
            ? `History starts ${shortDay(d.first_day)} ${d.first_day.slice(0, 4)}.`
            : "Tickets still open or pending at the end of each day."}
        </p>
        <GetCsvButton
          path="reports/backlog"
          query={query}
          fallback={`cedesk-backlog-report-${params.from}-to-${params.to}.csv`}
        />
      </div>

      <section aria-label="Backlog trend">
        <SectionHeader
          icon={TrendIcon}
          title="Backlog over time"
          description="Open, pending and breaching tickets at the end of each day."
        />
        <Card className="space-y-2 p-4 sm:p-5">
          <Legend series={SERIES} />
          <LineChart
            ariaLabel="Open, pending and breaching tickets at the end of each day"
            labels={days.map(shortDay)}
            series={SERIES.map((s) => ({
              name: s.name,
              tone: s.tone,
              values: days.map((day) => byDay.get(day)?.[s.key] ?? null),
            }))}
            unit="count"
            height={240}
          />
          <TimezoneNote timezone={d.timezone} />
        </Card>
      </section>

      <section aria-label="Daily snapshots">
        <SectionHeader
          icon={Table2}
          title="Daily snapshots"
          description="One row per day, newest first."
        />
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[20rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th scope="col" className="px-4 py-2.5 font-medium">
                  Day
                </th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">
                  Open
                </th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">
                  Pending
                </th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">
                  Breaching
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {[...d.days].reverse().map((r) => (
                <tr key={r.day} className="hover:bg-surface-2/60">
                  <th scope="row" className="px-4 py-2.5 text-left font-medium">
                    {r.day}
                  </th>
                  <td className="px-4 py-2.5 text-right tabular-nums">{r.open.toLocaleString()}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    {r.pending.toLocaleString()}
                  </td>
                  <td
                    className={cn(
                      "px-4 py-2.5 text-right tabular-nums",
                      r.breaching > 0 && "font-medium text-danger",
                    )}
                  >
                    {r.breaching.toLocaleString()}
                  </td>
                </tr>
              ))}
              {d.days.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-6 text-center text-muted-foreground">
                    {DASH} No snapshots in this range.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </Card>
      </section>
    </div>
  );
}

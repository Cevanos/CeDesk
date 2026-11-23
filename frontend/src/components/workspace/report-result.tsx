"use client";

import { AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";

import { Card } from "@/components/ui/primitives";
import {
  DASH,
  fillDayGaps,
  formatValue,
  groupLabel,
  isTimeGroup,
  type ReportGroup,
  type ReportResult,
  type Unit,
} from "@/lib/reports";
import { cn } from "@/lib/utils";

/* ---------- line chart (no chart library: plain SVG) ---------- */

export interface Series {
  name: string;
  /** A Tailwind text colour class; the line uses `currentColor`. */
  tone: string;
  /** `null` is a gap in the line, never a 0. */
  values: (number | null)[];
}

const W = 640;
const PAD = { l: 44, r: 12, t: 10, b: 24 };

/** A line per series over shared x labels. A missing value breaks the line. */
export function LineChart({
  labels,
  series,
  unit,
  height = 200,
  ariaLabel,
  detail,
}: {
  labels: string[];
  series: Series[];
  unit: Unit;
  height?: number;
  ariaLabel: string;
  /** Extra text for a point's tooltip, e.g. "based on 20". */
  detail?: (seriesIndex: number, i: number) => string;
}) {
  const n = labels.length;
  const all = series.flatMap((s) => s.values).filter((v): v is number => v !== null);
  const max = Math.max(1, ...all);
  const innerW = W - PAD.l - PAD.r;
  const innerH = height - PAD.t - PAD.b;
  const x = (i: number) => PAD.l + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
  const y = (v: number) => PAD.t + innerH - (v / max) * innerH;
  const showDots = n <= 45;
  // About six x labels, whatever the length.
  const step = Math.max(1, Math.ceil(n / 6));

  return (
    <svg
      viewBox={`0 0 ${W} ${height}`}
      role="img"
      aria-label={ariaLabel}
      className="h-auto w-full"
      preserveAspectRatio="xMidYMid meet"
    >
      {[0, 0.5, 1].map((f) => (
        <g key={f}>
          <line
            x1={PAD.l}
            x2={W - PAD.r}
            y1={y(max * f)}
            y2={y(max * f)}
            className="stroke-border"
            strokeWidth={1}
          />
          <text
            x={PAD.l - 6}
            y={y(max * f) + 3.5}
            textAnchor="end"
            className="fill-muted-foreground text-[10px]"
          >
            {formatValue(max * f, unit)}
          </text>
        </g>
      ))}
      {labels.map((l, i) =>
        i % step === 0 ? (
          <text
            key={i}
            x={x(i)}
            y={height - 6}
            textAnchor="middle"
            className="fill-muted-foreground text-[10px]"
          >
            {l.replace(/^Week of /, "")}
          </text>
        ) : null,
      )}
      {series.map((s) => {
        // Consecutive real values make one path; a gap starts the next.
        const segs: [number, number][][] = [];
        let cur: [number, number][] = [];
        s.values.forEach((v, i) => {
          if (v === null) {
            if (cur.length) segs.push(cur);
            cur = [];
          } else cur.push([x(i), y(v)]);
        });
        if (cur.length) segs.push(cur);
        return (
          <g key={s.name} className={s.tone}>
            {segs.map((seg, k) =>
              seg.length > 1 ? (
                <path
                  key={k}
                  d={seg.map(([px, py], j) => `${j ? "L" : "M"}${px},${py}`).join(" ")}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              ) : null,
            )}
            {s.values.map((v, i) =>
              v !== null && (showDots || segs.some((seg) => seg.length === 1)) ? (
                <circle key={i} cx={x(i)} cy={y(v)} r={showDots ? 2.5 : 2} fill="currentColor" />
              ) : null,
            )}
          </g>
        );
      })}
      {/* One invisible column per x with the values in its tooltip (and for screen readers). */}
      {labels.map((l, i) => (
        <rect
          key={i}
          x={x(i) - innerW / Math.max(1, n - 1) / 2}
          y={PAD.t}
          width={innerW / Math.max(1, n - 1)}
          height={innerH}
          fill="transparent"
        >
          <title>
            {l}
            {series
              .map(
                (s, si) =>
                  `\n${series.length > 1 ? `${s.name}: ` : ""}${formatValue(s.values[i], unit)}${
                    detail ? detail(si, i) : ""
                  }`,
              )
              .join("")}
          </title>
        </rect>
      ))}
    </svg>
  );
}

export function Legend({ series }: { series: readonly Pick<Series, "name" | "tone">[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {series.map((s) => (
        <li key={s.name} className="flex items-center gap-1.5">
          <span className={cn("inline-block h-0.5 w-4 rounded bg-current", s.tone)} aria-hidden />
          {s.name}
        </li>
      ))}
    </ul>
  );
}

/* ---------- horizontal bars ---------- */

export function HBars({
  rows,
  unit,
}: {
  rows: { key: string; label: string; value: number | null; n: number }[];
  unit: Unit;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value ?? 0));
  return (
    <ul className="space-y-2">
      {rows.map((r) => (
        <li key={r.key} className="flex items-center gap-3 text-sm">
          <span className="w-32 shrink-0 truncate font-medium sm:w-40" title={r.label}>
            {r.label}
          </span>
          <span
            className="h-2.5 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-3"
            title={r.value === null ? "Nothing to measure" : `Based on ${r.n}`}
          >
            {r.value !== null && (
              <span
                className="block h-full rounded-full bg-primary"
                style={{ width: `${Math.max(1, (r.value / max) * 100)}%` }}
              />
            )}
          </span>
          <span className="w-20 shrink-0 text-right text-muted-foreground tabular-nums">
            {formatValue(r.value, unit)}
          </span>
        </li>
      ))}
    </ul>
  );
}

/* ---------- notices ---------- */

const archiveLine = (r: Pick<ReportResult, "incomplete" | "notes">) =>
  r.incomplete
    ? (r.notes.find((n) => /archive/i.test(n)) ??
      "This range reaches back past the archive age, so older numbers can be too low.")
    : null;

/** The small yellow notice above a chart, when the range reaches past the archive age. */
export function ArchiveNotice({ result }: { result: Pick<ReportResult, "incomplete" | "notes"> }) {
  const line = archiveLine(result);
  return line ? (
    <p
      role="note"
      className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning"
    >
      <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
      {line}
    </p>
  ) : null;
}

/** Every line of `notes` under the chart, in small grey text (the archive line is already above). */
export function NotesList({ result }: { result: Pick<ReportResult, "incomplete" | "notes"> }) {
  const archive = archiveLine(result);
  const rest = result.notes.filter((n) => n !== archive);
  return rest.length > 0 ? (
    <ul className="space-y-0.5 text-xs text-muted-foreground">
      {rest.map((n, i) => (
        <li key={i}>{n}</li>
      ))}
    </ul>
  ) : null;
}

/* ---------- a whole result ---------- */

export function TimezoneNote({ timezone }: { timezone: string | null | undefined }) {
  return timezone ? <p className="text-xs text-muted-foreground">Times in {timezone}</p> : null;
}

/**
 * One report answer: the total, a chart (a line for day/week/month, bars for anything else),
 * the notices and the time zone. `compact` is for a dashboard card.
 */
export function ResultView({
  result,
  compact = false,
  actions,
}: {
  result: ReportResult;
  compact?: boolean;
  actions?: ReactNode;
}) {
  const timeGrouped = isTimeGroup(result.group_by);
  // Backlog snapshots are only there for days that had one: show the others as gaps.
  const groups: ReportGroup[] =
    timeGrouped && result.group_by === "day" && result.metric === "backlog"
      ? fillDayGaps(result.groups)
      : result.groups;
  const rows = groups.map((g, i) => ({
    key: g.key ?? `none-${i}`,
    label: groupLabel(g, result.group_by),
    value: g.value,
    n: g.n,
  }));
  const hasTotal = result.total !== null && result.total !== undefined;
  const empty = rows.length === 0 || rows.every((r) => r.value === null);

  return (
    <div className="space-y-3">
      <ArchiveNotice result={result} />
      <div className="flex flex-wrap items-end justify-between gap-3">
        {hasTotal ? (
          <div>
            <p
              className={cn(
                "font-semibold tracking-tight tabular-nums",
                compact ? "text-2xl" : "text-3xl",
              )}
            >
              {formatValue(result.total, result.unit)}
            </p>
            {result.total_n !== null && (
              <p className="text-xs text-muted-foreground">
                {result.label}, based on {result.total_n.toLocaleString()}
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm font-medium">{result.label}</p>
        )}
        {actions}
      </div>

      {result.group_by !== "none" &&
        (empty ? (
          <p className="text-sm text-muted-foreground">No data for this range.</p>
        ) : timeGrouped ? (
          <LineChart
            ariaLabel={`${result.label} by ${result.group_by}`}
            labels={rows.map((r) => r.label)}
            series={[
              { name: result.label, tone: "text-primary", values: rows.map((r) => r.value) },
            ]}
            unit={result.unit}
            height={compact ? 130 : 220}
            detail={(_s, i) => (rows[i]!.value === null ? "" : ` (based on ${rows[i]!.n})`)}
          />
        ) : (
          <>
            <HBars rows={compact ? rows.slice(0, 6) : rows} unit={result.unit} />
            {result.truncated && (
              <p className="text-xs text-muted-foreground">Showing the biggest 50.</p>
            )}
          </>
        ))}

      {!compact && timeGrouped && !empty && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
            Show as a table
          </summary>
          <div className="mt-2 max-h-72 overflow-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th scope="col" className="px-3 py-2 font-medium">
                    {result.group_by === "day"
                      ? "Day"
                      : result.group_by === "week"
                        ? "Week"
                        : "Month"}
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    {result.label}
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Based on
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((r) => (
                  <tr key={r.key}>
                    <th scope="row" className="px-3 py-1.5 text-left font-normal">
                      {r.label}
                    </th>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {formatValue(r.value, result.unit)}
                    </td>
                    <td className="px-3 py-1.5 text-right text-muted-foreground tabular-nums">
                      {r.value === null ? DASH : r.n.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      <NotesList result={result} />
      <TimezoneNote timezone={result.timezone} />
    </div>
  );
}

/** A result in a bordered card with a title. */
export function ResultCard({ children }: { children: ReactNode }) {
  return <Card className="p-4 sm:p-5">{children}</Card>;
}

"use client";

import { ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState, Input, Select, Skeleton } from "@/components/ui/primitives";
import { LoadError } from "@/components/workspace/list-shared";
import { AiPanel, CsvButton, MetricsPanel } from "@/components/workspace/reports-more";
import { ReportSkeleton, ReportData, ArticlesTable } from "@/components/workspace/reports-sections";
import { useCurrentUser } from "@/hooks/use-auth";
import { useTeamReport, useTeams } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

const PRESETS = [
  { id: "7", label: "7 days", days: 7 },
  { id: "30", label: "30 days", days: 30 },
  { id: "90", label: "90 days", days: 90 },
] as const;
type PresetId = (typeof PRESETS)[number]["id"] | "custom";
const TABS = [
  { id: "team", label: "Team" },
  { id: "metrics", label: "More metrics" },
  { id: "ai", label: "AI" },
] as const;
type TabId = (typeof TABS)[number]["id"];

/** YYYY-MM-DD in the viewer's local time. */
function ymd(d: Date) {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function daysAgo(today: Date, n: number) {
  const d = new Date(today);
  d.setDate(d.getDate() - n);
  return ymd(d);
}

export function ReportsView() {
  const { perms, isPending } = useCurrentUser();
  if (isPending) return <Skeleton className="m-6 h-60" />;
  if (!perms.viewReports) return <SupervisorsOnly />;
  return <ReportsBody />;
}

function SupervisorsOnly() {
  return (
    <EmptyState icon={<ShieldAlert className="size-6" />} title="Supervisors and Admins only">
      You don&apos;t have access to team reports.
    </EmptyState>
  );
}

function ReportsBody() {
  const [today] = useState(() => new Date());
  const [preset, setPreset] = useState<PresetId>("30");
  const [custom, setCustom] = useState({ from: daysAgo(today, 29), to: ymd(today) });
  const [teamId, setTeamId] = useState("");
  const [tab, setTab] = useState<TabId>("team");
  const teams = useTeams();

  const range = useMemo(() => {
    if (preset === "custom") return custom;
    const days = PRESETS.find((p) => p.id === preset)?.days ?? 30;
    return { from: daysAgo(today, days - 1), to: ymd(today) };
  }, [preset, custom, today]);

  const badRange = !range.from || !range.to || range.from > range.to;
  const params = { ...range, team_id: teamId || undefined };
  const report = useTeamReport(params);

  return (
    <div className="mx-auto w-full max-w-6xl animate-rise px-3 py-5 sm:px-6 sm:py-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Reports</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            How the team is doing: volume, speed, SLA and customer satisfaction.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div
            role="group"
            aria-label="Period"
            className="inline-flex rounded-xl border border-border bg-surface-2 p-1"
          >
            {[...PRESETS, { id: "custom" as const, label: "Custom" }].map((p) => (
              <button
                key={p.id}
                type="button"
                aria-pressed={preset === p.id}
                onClick={() => setPreset(p.id)}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-[13px] font-semibold transition-all",
                  preset === p.id
                    ? "bg-surface text-foreground shadow-card ring-1 ring-border"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="w-44">
            <Select
              aria-label="Team"
              value={teamId}
              onChange={(e) => setTeamId(e.target.value)}
              options={[
                { value: "", label: "All teams" },
                ...(teams.data ?? []).map((t) => ({ value: t.id, label: t.name })),
              ]}
            />
          </div>
        </div>
      </div>

      {preset === "custom" && (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <label htmlFor="rep-from">From</label>
            <Input
              id="rep-from"
              type="date"
              className="w-40"
              value={custom.from}
              max={custom.to || undefined}
              onChange={(e) => setCustom((c) => ({ ...c, from: e.target.value }))}
            />
          </div>
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <label htmlFor="rep-to">To</label>
            <Input
              id="rep-to"
              type="date"
              className="w-40"
              value={custom.to}
              min={custom.from || undefined}
              onChange={(e) => setCustom((c) => ({ ...c, to: e.target.value }))}
            />
          </div>
          {badRange && (
            <p role="alert" className="text-xs text-danger">
              Pick a start date that is not after the end date.
            </p>
          )}
        </div>
      )}

      <div
        role="tablist"
        aria-label="Report"
        className="mt-6 inline-flex rounded-xl border border-border bg-surface-2 p-1"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "rounded-lg px-3 py-1.5 text-[13px] font-semibold transition-all",
              tab === t.id
                ? "bg-surface text-foreground shadow-card ring-1 ring-border"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="mt-6" aria-live="polite">
        {badRange ? null : tab === "metrics" ? (
          <MetricsPanel params={params} />
        ) : tab === "ai" ? (
          <AiPanel params={params} />
        ) : (
          <>
            {report.isPending ? (
              <ReportSkeleton />
            ) : report.isError ? (
              report.error instanceof ApiError && report.error.status === 403 ? (
                <SupervisorsOnly />
              ) : (
                <LoadError error={report.error} />
              )
            ) : (
              <>
                <div className="mb-4 flex justify-end">
                  <CsvButton path="reports/team" params={params} withTeam name="team-report" />
                </div>
                <ReportData data={report.data} stale={report.isPlaceholderData} />
              </>
            )}
            {report.isPending || report.isError ? null : <ArticlesTable range={range} />}
          </>
        )}
      </div>
    </div>
  );
}

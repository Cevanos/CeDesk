"use client";

import {
  ArrowDown,
  ArrowUp,
  BarChart3,
  BookOpen,
  CheckCircle2,
  Clock,
  Hourglass,
  Inbox,
  ShieldAlert,
  ShieldCheck,
  Smile,
  Timer,
  type LucideIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Stars } from "@/components/tickets/csat";
import { formatMinutes } from "@/components/tickets/sla";
import { Badge, Card, EmptyState, Input, Select, Skeleton } from "@/components/ui/primitives";
import { LoadError } from "@/components/workspace/list-shared";
import { AiPanel, CsvButton, MetricsPanel } from "@/components/workspace/reports-more";
import { useCurrentUser } from "@/hooks/use-auth";
import { useArticleReport, useTeamReport, useTeams } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { ReportAgentRow, ReportArticleRow, Role, TeamReport } from "@/lib/api/types";
import { ROLE_LABEL } from "@/lib/permissions";
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

const dash = "—";
const count = (n: number | null) => (n === null ? dash : n.toLocaleString());
const mins = (n: number | null) => (n === null ? dash : formatMinutes(n));
const percent = (n: number | null) => (n === null ? dash : `${Math.round(n * 10) / 10}%`);
const rating = (n: number | null) => (n === null ? dash : n.toFixed(1));

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
  const [custom, setCustom] = useState({ from: daysAgo(today, 30), to: ymd(today) });
  const [teamId, setTeamId] = useState("");
  const [tab, setTab] = useState<TabId>("team");
  const teams = useTeams();

  const range = useMemo(() => {
    if (preset === "custom") return custom;
    const days = PRESETS.find((p) => p.id === preset)?.days ?? 30;
    return { from: daysAgo(today, days), to: ymd(today) };
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

function ReportSkeleton() {
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} className="h-24 rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-56 rounded-xl" />
      <Skeleton className="h-48 rounded-xl" />
    </div>
  );
}

function Tile({
  icon: Icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  hint?: string;
  tone: string;
}) {
  return (
    <Card className="group relative overflow-hidden p-4 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-pop">
      <div className="flex items-center gap-3">
        <span
          className={cn(
            "grid size-10 shrink-0 place-items-center rounded-xl ring-1 ring-black/5 ring-inset",
            tone,
          )}
        >
          <Icon className="size-[18px]" aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="text-[11px] font-medium tracking-wider text-subtle-foreground uppercase">
            {label}
          </p>
          <p className="truncate text-xl leading-tight font-semibold tracking-tight tabular-nums">
            {value}
          </p>
          {hint && <p className="truncate text-xs text-muted-foreground">{hint}</p>}
        </div>
      </div>
    </Card>
  );
}

function ReportData({ data, stale }: { data: TeamReport; stale: boolean }) {
  const s = data.summary;
  return (
    <div className={cn("space-y-6 transition-opacity", stale && "opacity-60")}>
      <section aria-label="Summary" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile
          icon={Inbox}
          label="Created"
          value={count(s.created)}
          tone="bg-primary-soft text-primary-ink"
        />
        <Tile
          icon={CheckCircle2}
          label="Resolved"
          value={count(s.resolved)}
          tone="bg-success-soft text-success"
        />
        <Tile
          icon={Hourglass}
          label="Open now"
          value={count(s.open)}
          tone="bg-info-soft text-info"
        />
        <Tile
          icon={ShieldAlert}
          label="Open breaching"
          value={count(s.open_breaching)}
          hint="Open tickets past a target"
          tone="bg-danger-soft text-danger"
        />
        <Tile
          icon={ShieldCheck}
          label="SLA met"
          value={percent(s.sla_met_pct)}
          tone="bg-success-soft text-success"
        />
        <Tile
          icon={Timer}
          label="First reply"
          value={mins(s.first_reply_minutes)}
          hint="Average"
          tone="bg-warning-soft text-warning"
        />
        <Tile
          icon={Clock}
          label="Resolution"
          value={mins(s.resolve_minutes)}
          hint="Average"
          tone="bg-warning-soft text-warning"
        />
        <Tile
          icon={Smile}
          label="CSAT"
          value={s.csat_average === null ? dash : `${rating(s.csat_average)} ★`}
          hint={
            s.csat_count
              ? `${s.csat_count} ratings${s.csat_happy_pct != null ? ` · ${percent(s.csat_happy_pct)} happy` : ""}`
              : "No ratings yet"
          }
          tone="bg-amber-100 text-amber-700"
        />
      </section>

      <CsatBars data={data} />

      <section aria-labelledby="by-team">
        <h2 id="by-team" className="mb-2 text-base font-semibold">
          Tickets by team
        </h2>
        <Card className="overflow-x-auto">
          {data.teams.length === 0 ? (
            <EmptyState icon={<BarChart3 className="size-6" />} title="No team data">
              Nothing to show for this period.
            </EmptyState>
          ) : (
            <table className="w-full min-w-[36rem] text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <Th>Team</Th>
                  <Th right>Created</Th>
                  <Th right>Resolved</Th>
                  <Th right>Open</Th>
                  <Th right>SLA met</Th>
                  <Th right>CSAT</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.teams.map((t) => (
                  <tr key={t.id ?? t.name} className="hover:bg-surface-2/60">
                    <th scope="row" className="px-4 py-3 text-left font-medium">
                      {t.name}
                    </th>
                    <Td>{count(t.created)}</Td>
                    <Td>{count(t.resolved)}</Td>
                    <Td>{count(t.open)}</Td>
                    <Td>{percent(t.sla_met_pct)}</Td>
                    <Td>{rating(t.csat_average)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </section>

      <AgentsTable agents={data.agents} />
    </div>
  );
}

function Th({
  children,
  right,
  ...rest
}: React.ThHTMLAttributes<HTMLTableCellElement> & { right?: boolean }) {
  return (
    <th scope="col" className={cn("px-4 py-2.5 font-medium", right && "text-right")} {...rest}>
      {children}
    </th>
  );
}
function Td({ children }: { children: React.ReactNode }) {
  return <td className="px-4 py-3 text-right tabular-nums">{children}</td>;
}

function CsatBars({ data }: { data: TeamReport }) {
  const total = data.csat.reduce((n, c) => n + c.count, 0);
  const max = Math.max(1, ...data.csat.map((c) => c.count));
  const average = data.summary.csat_average;
  return (
    <section aria-labelledby="csat-h">
      <h2 id="csat-h" className="mb-2 text-base font-semibold">
        Customer satisfaction
      </h2>
      <Card className="grid gap-6 p-5 sm:grid-cols-[11rem_minmax(0,1fr)]">
        <div className="flex flex-col items-start justify-center gap-1">
          <span className="text-4xl font-semibold tracking-tight tabular-nums">
            {rating(average)}
          </span>
          {average !== null && <Stars score={average} size="size-5" />}
          <span className="text-sm text-muted-foreground">
            {total ? `${total} ${total === 1 ? "rating" : "ratings"}` : "No ratings yet"}
          </span>
        </div>
        <ul className="space-y-2">
          {[...data.csat].reverse().map((c) => {
            const share = total ? Math.round((c.count / total) * 100) : 0;
            return (
              <li key={c.score} className="flex items-center gap-3 text-sm">
                <span className="w-10 shrink-0 font-medium tabular-nums">{c.score} ★</span>
                <div
                  role="meter"
                  aria-label={`${c.score} star ratings`}
                  aria-valuemin={0}
                  aria-valuemax={total}
                  aria-valuenow={c.count}
                  aria-valuetext={`${c.count} (${share}%)`}
                  className="h-2.5 flex-1 overflow-hidden rounded-full bg-surface-3"
                >
                  <div
                    className={cn(
                      "h-full rounded-full transition-[width] duration-500",
                      c.score >= 4 ? "bg-success" : c.score === 3 ? "bg-warning" : "bg-danger",
                    )}
                    style={{ width: `${(c.count / max) * 100}%` }}
                  />
                </div>
                <span className="w-20 shrink-0 text-right text-muted-foreground tabular-nums">
                  {c.count} · {share}%
                </span>
              </li>
            );
          })}
        </ul>
      </Card>
    </section>
  );
}

type AgentSort = "name" | "resolved" | "open" | "first_reply_minutes" | "csat_average";

function AgentsTable({ agents }: { agents: ReportAgentRow[] }) {
  const [sort, setSort] = useState<{ key: AgentSort; dir: 1 | -1 }>({ key: "resolved", dir: -1 });

  const sorted = useMemo(() => {
    const { key, dir } = sort;
    return [...agents].sort((a, b) => {
      if (key === "name") return a.name.localeCompare(b.name) * dir;
      const x = a[key];
      const y = b[key];
      // Empty values always sink to the bottom.
      if (x === null && y === null) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return (x - y) * dir;
    });
  }, [agents, sort]);

  const head = (key: AgentSort, label: string, right = true) => {
    const active = sort.key === key;
    return (
      <th
        scope="col"
        aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
        className={cn("px-4 py-2.5 font-medium", right && "text-right")}
      >
        <button
          type="button"
          onClick={() =>
            setSort((s) => ({
              key,
              dir: s.key === key ? (s.dir === 1 ? -1 : 1) : key === "name" ? 1 : -1,
            }))
          }
          className={cn(
            "inline-flex items-center gap-1 hover:text-foreground",
            active && "text-foreground",
          )}
        >
          {label}
          {active &&
            (sort.dir === 1 ? (
              <ArrowUp className="size-3" aria-hidden />
            ) : (
              <ArrowDown className="size-3" aria-hidden />
            ))}
        </button>
      </th>
    );
  };

  return (
    <section aria-labelledby="agents-h">
      <h2 id="agents-h" className="mb-2 text-base font-semibold">
        Agents
      </h2>
      <Card className="overflow-x-auto">
        {sorted.length === 0 ? (
          <EmptyState icon={<BarChart3 className="size-6" />} title="No agent data">
            Nothing to show for this period.
          </EmptyState>
        ) : (
          <table className="w-full min-w-[40rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                {head("name", "Agent", false)}
                <Th>Role</Th>
                <Th>Status</Th>
                {head("resolved", "Resolved")}
                {head("open", "Open")}
                {head("first_reply_minutes", "First reply")}
                {head("csat_average", "CSAT")}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {sorted.map((a) => (
                <tr key={a.id ?? a.name} className="hover:bg-surface-2/60">
                  <th scope="row" className="px-4 py-3 text-left font-medium">
                    {a.name}
                    {a.team_name && <Badge className="ml-2">{a.team_name}</Badge>}
                  </th>
                  <td className="px-4 py-3 text-muted-foreground">
                    {a.role ? (ROLE_LABEL[a.role as Role] ?? a.role) : dash}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground capitalize">
                    {a.status ? a.status.replaceAll("_", " ") : dash}
                  </td>
                  <Td>{count(a.resolved)}</Td>
                  <Td>{count(a.open)}</Td>
                  <Td>{mins(a.first_reply_minutes)}</Td>
                  <Td>{rating(a.csat_average)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </section>
  );
}

function ArticlesTable({ range }: { range: { from: string; to: string } }) {
  const q = useArticleReport(range);
  const rows: ReportArticleRow[] = useMemo(
    () =>
      [...(q.data ?? [])].sort((a, b) => b.helpful + b.not_helpful - (a.helpful + a.not_helpful)),
    [q.data],
  );

  return (
    <section
      aria-labelledby="articles-h"
      className={cn("mt-6", q.isPlaceholderData && "opacity-60")}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 id="articles-h" className="text-base font-semibold">
          Help articles
        </h2>
        <CsvButton path="reports/articles" params={range} withTeam={false} name="articles" />
      </div>
      <Card className="overflow-x-auto">
        {q.isPending ? (
          <Skeleton className="h-24" />
        ) : q.isError ? (
          <LoadError error={q.error} />
        ) : rows.length === 0 ? (
          <EmptyState icon={<BookOpen className="size-6" />} title="No article votes">
            Nobody has voted on a help article in this period.
          </EmptyState>
        ) : (
          <table className="w-full min-w-[30rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <Th>Article</Th>
                <Th right>Helpful</Th>
                <Th right>Not helpful</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((a) => (
                <tr key={a.id ?? a.title} className="hover:bg-surface-2/60">
                  <th scope="row" className="px-4 py-3 text-left font-medium">
                    {a.title}
                  </th>
                  <Td>{count(a.helpful)}</Td>
                  <Td>{count(a.not_helpful)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </section>
  );
}

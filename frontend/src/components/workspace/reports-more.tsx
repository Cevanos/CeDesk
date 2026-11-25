"use client";

import { Download, ExternalLink, Gauge, Hourglass, PieChart, Users } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { AiMark } from "@/components/ui/ai";
import { Button, Card, EmptyState, Skeleton } from "@/components/ui/primitives";
import { SectionHeader } from "@/components/ui/section";
import { DataTable, RowTh, TBody, Td, Th, THead, Tr } from "@/components/ui/table";
import { LoadError } from "@/components/workspace/list-shared";
import {
  csvQuery,
  useAiReport,
  useMetricsReport,
  type Labelled,
  type MetricTile,
  type MetricsAgentRow,
  type MetricsReport,
  type ReportParams,
} from "@/hooks/workspace/reports";
import { ApiError, downloadFile } from "@/lib/api/client";
import { duration } from "@/lib/reports";
import { safeHttpUrl } from "@/lib/safe-url";
import { cn } from "@/lib/utils";

const dash = "—";
const count = (n: number | null) => (n === null ? dash : n.toLocaleString());

function tileValue(t: MetricTile): string {
  if (t.value === null) return dash;
  if (t.unit === "minutes") return duration(t.value);
  if (t.unit === "percent") return `${Math.round(t.value * 10) / 10}%`;
  return t.value.toLocaleString();
}

/** Downloads a report as a file through the session (a plain link would get a 401). */
export function CsvButton({
  path,
  params,
  withTeam,
  name,
}: {
  path: string;
  params: ReportParams;
  withTeam: boolean;
  name: string;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Button
      size="sm"
      variant="outline"
      loading={busy}
      onClick={() => {
        setBusy(true);
        downloadFile(path, `${name}-${params.from}-to-${params.to}.csv`, csvQuery(params, withTeam))
          .catch((e) => toast.error(e instanceof ApiError ? e.userMessage : "Download failed"))
          .finally(() => setBusy(false));
      }}
    >
      {!busy && <Download className="size-3.5" aria-hidden />} Download CSV
    </Button>
  );
}

function Stat({
  label,
  value,
  hint,
  ai,
}: {
  label: string;
  value: string;
  hint?: string;
  ai?: boolean;
}) {
  return (
    <Card
      className={cn(
        "group relative overflow-hidden p-4 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-pop",
        ai && "ai-frame",
      )}
    >
      {ai && (
        <span
          aria-hidden
          className="pointer-events-none absolute -top-10 -right-10 size-24 rounded-full bg-ai-gradient opacity-10 blur-2xl transition-opacity group-hover:opacity-25"
        />
      )}
      <p className="relative text-[11px] font-medium tracking-wider text-subtle-foreground uppercase">
        {label}
      </p>
      <p
        className={cn(
          "relative mt-0.5 truncate text-2xl font-semibold tracking-tight tabular-nums",
          ai && "text-ai-gradient",
        )}
      >
        {value}
      </p>
      {hint && <p className="relative truncate text-xs text-muted-foreground">{hint}</p>}
    </Card>
  );
}

function Bars({
  title,
  description,
  rows,
}: {
  title: string;
  description?: string;
  rows: Labelled[];
}) {
  const max = Math.max(1, ...rows.map((r) => r.open));
  return (
    <section aria-label={title}>
      <SectionHeader icon={Hourglass} title={title} description={description} />
      <Card className="p-4">
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No data for this period</p>
        ) : (
          <ul className="space-y-2">
            {rows.map((r) => (
              <li key={r.label} className="flex items-center gap-3 text-sm">
                <span className="w-32 shrink-0 truncate font-medium" title={r.label}>
                  {r.label}
                </span>
                <span className="h-2.5 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-3">
                  <span
                    className="block h-full rounded-full bg-primary"
                    style={{ width: `${(r.open / max) * 100}%` }}
                  />
                </span>
                <span className="w-12 shrink-0 text-right text-muted-foreground tabular-nums">
                  {r.open.toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </section>
  );
}

/** Channels / Topics / Sentiment behind three switch buttons. */
function Breakdown({ d }: { d: MetricsReport }) {
  const tabs = [
    { key: "channels", label: "Channels", rows: d.channels },
    { key: "topics", label: "Topics", rows: d.topics },
    { key: "sentiment", label: "Sentiment", rows: d.sentiment },
  ] as const;
  const [active, setActive] = useState<(typeof tabs)[number]["key"]>("channels");
  const cur = tabs.find((t) => t.key === active)!;
  return (
    <section aria-label="Breakdown">
      <SectionHeader
        icon={PieChart}
        title="Breakdown"
        description="Where tickets come from and how customers feel."
        aside={
          <div className="flex flex-wrap gap-1.5">
            {tabs.map((t) => (
              <Button
                key={t.key}
                size="sm"
                variant={t.key === active ? "primary" : "outline"}
                aria-pressed={t.key === active}
                onClick={() => setActive(t.key)}
              >
                {t.label}
              </Button>
            ))}
          </div>
        }
      />
      <Card>
        {cur.rows.length === 0 ? (
          <EmptyState title="No data for this period">Nothing to show.</EmptyState>
        ) : (
          <DataTable minWidth="20rem">
            <THead>
              <tr>
                <Th>{cur.label.replace(/s$/, "")}</Th>
                <Th right>Created</Th>
                <Th right>Open</Th>
              </tr>
            </THead>
            <TBody>
              {cur.rows.map((r) => (
                <Tr key={r.label}>
                  <RowTh>{r.label}</RowTh>
                  <Td right>{count(r.created)}</Td>
                  <Td right>{r.open.toLocaleString()}</Td>
                </Tr>
              ))}
            </TBody>
          </DataTable>
        )}
      </Card>
    </section>
  );
}

function AgentsMetrics({ agents }: { agents: MetricsAgentRow[] }) {
  return (
    <section aria-label="Agents">
      <SectionHeader
        icon={Users}
        title="Agents: time and tasks"
        description="How each agent spent their time."
      />
      <Card>
        {agents.length === 0 ? (
          <EmptyState title="No agent data">Nothing to show for this period.</EmptyState>
        ) : (
          <DataTable minWidth="28rem">
            <THead>
              <tr>
                <Th>Agent</Th>
                <Th right>Time logged</Th>
                <Th right>Billable</Th>
                <Th right>Open tasks</Th>
                <Th right>Overdue</Th>
              </tr>
            </THead>
            <TBody>
              {agents.map((a) => (
                <Tr key={a.id ?? a.name}>
                  <RowTh>{a.name}</RowTh>
                  <Td right>{a.logged_minutes === null ? dash : duration(a.logged_minutes)}</Td>
                  <Td right>{a.billable_minutes === null ? dash : duration(a.billable_minutes)}</Td>
                  <Td right>{count(a.open_tasks)}</Td>
                  <Td right className={cn((a.overdue_tasks ?? 0) > 0 && "font-medium text-danger")}>
                    {count(a.overdue_tasks)}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </DataTable>
        )}
      </Card>
    </section>
  );
}

/** "More metrics" tab. */
export function MetricsPanel({ params }: { params: ReportParams }) {
  const q = useMetricsReport(params);
  if (q.isPending) return <Skeleton className="h-64 rounded-xl" />;
  if (q.isError) return <LoadError error={q.error} />;
  const d = q.data;
  const governanceUrl = safeHttpUrl(d.governance_url);
  return (
    <div className={cn("space-y-10 transition-opacity", q.isPlaceholderData && "opacity-60")}>
      <div className="flex flex-wrap items-center justify-end gap-2">
        {governanceUrl && (
          <a
            href={governanceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border-strong bg-surface px-3 text-[13px] font-medium hover:bg-surface-2"
          >
            Governance <ExternalLink className="size-3.5" aria-hidden />
          </a>
        )}
        <CsvButton path="reports/metrics" params={params} withTeam name="more-metrics" />
      </div>
      {d.tiles.length > 0 && (
        <section aria-label="Summary">
          <SectionHeader icon={Gauge} title="Summary" description="Key figures for the period." />
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {d.tiles.map((t) => (
              <Stat key={t.key} label={t.label} value={tileValue(t)} hint={t.hint} />
            ))}
          </div>
        </section>
      )}
      <Bars
        title="Open tickets by age"
        description="How long open tickets have been waiting."
        rows={d.backlog}
      />
      <Breakdown d={d} />
      <AgentsMetrics agents={d.agents} />
    </div>
  );
}

/** "AI" tab: what the AI assistant did. */
export function AiPanel({ params }: { params: ReportParams }) {
  const q = useAiReport(params);
  if (q.isPending) return <Skeleton className="h-28 rounded-xl" />;
  if (q.isError) return <LoadError error={q.error} />;
  const d = q.data;
  return (
    <div className="space-y-6">
      <div className="ai-frame relative flex flex-wrap items-center gap-3 overflow-hidden rounded-2xl p-4 shadow-card sm:p-5">
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-ai-gradient opacity-[0.06]"
        />
        <AiMark className="relative size-10 rounded-xl [&_svg]:size-5" />
        <div className="relative min-w-0 flex-1 basis-60">
          <h2 className="text-[15px] font-semibold">Cevanos AI assistant</h2>
          <p className="text-sm text-muted-foreground">
            How the AI is handling customer chats before they reach your team.
          </p>
        </div>
        <div className="relative">
          <CsvButton path="reports/ai" params={params} withTeam={false} name="cedesk-ai-report" />
        </div>
      </div>
      <section
        aria-label="AI assistant"
        className={cn(
          "grid gap-3 transition-opacity sm:grid-cols-2 lg:grid-cols-4",
          q.isPlaceholderData && "opacity-60",
        )}
      >
        <Stat ai label="AI chats" value={count(d.chats)} />
        <Stat
          ai
          label="Handed to a person"
          value={count(d.handed_over)}
          hint={
            d.handed_over_percent === null
              ? undefined
              : `${d.handed_over_percent}% of finished chats`
          }
        />
        <Stat
          ai
          label="Resolved by AI"
          value={count(d.resolved_by_ai)}
          hint={
            d.resolved_by_ai_percent === null
              ? undefined
              : `${d.resolved_by_ai_percent}% of finished chats`
          }
        />
        <Stat
          ai
          label="CSAT of AI"
          value={d.csat_average === null ? dash : `${d.csat_average.toFixed(1)} ★`}
          hint={d.csat_count ? `${d.csat_count} ratings` : undefined}
        />
      </section>
    </div>
  );
}

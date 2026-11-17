"use client";

import { Download, ExternalLink } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { formatMinutes } from "@/components/tickets/sla";
import { Button, Card, EmptyState, Skeleton } from "@/components/ui/primitives";
import { LoadError } from "@/components/workspace/list-shared";
import {
  csvQuery,
  useAiReport,
  useMetricsReport,
  type Labelled,
  type MetricTile,
  type MetricsAgentRow,
  type ReportParams,
} from "@/hooks/workspace/reports";
import { ApiError, downloadFile } from "@/lib/api/client";
import { cn } from "@/lib/utils";

const dash = "—";
const count = (n: number | null) => (n === null ? dash : n.toLocaleString());

function tileValue(t: MetricTile): string {
  if (t.value === null) return dash;
  if (t.unit === "minutes") return formatMinutes(t.value);
  if (t.unit === "percent") return `${Math.round(t.value * 10) / 10}%`;
  if (t.unit === "rating") return t.value.toFixed(1);
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

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="p-4">
      <p className="text-[11px] font-medium tracking-wider text-subtle-foreground uppercase">
        {label}
      </p>
      <p className="mt-0.5 truncate text-xl font-semibold tracking-tight tabular-nums">{value}</p>
      {hint && <p className="truncate text-xs text-muted-foreground">{hint}</p>}
    </Card>
  );
}

function Bars({ title, rows }: { title: string; rows: Labelled[] }) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <section aria-label={title}>
      <h2 className="mb-2 text-base font-semibold">{title}</h2>
      <Card className="p-4">
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing to show for this period.</p>
        ) : (
          <ul className="space-y-2">
            {rows.map((r) => (
              <li key={r.label} className="flex items-center gap-3 text-sm">
                <span className="w-32 shrink-0 truncate font-medium capitalize" title={r.label}>
                  {r.label}
                </span>
                <span className="h-2.5 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-3">
                  <span
                    className="block h-full rounded-full bg-primary"
                    style={{ width: `${(r.count / max) * 100}%` }}
                  />
                </span>
                <span className="w-12 shrink-0 text-right text-muted-foreground tabular-nums">
                  {r.count.toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </section>
  );
}

function AgentsMetrics({ agents }: { agents: MetricsAgentRow[] }) {
  return (
    <section aria-label="Agents">
      <h2 className="mb-2 text-base font-semibold">Agents: time and tasks</h2>
      <Card className="overflow-x-auto">
        {agents.length === 0 ? (
          <EmptyState title="No agent data">Nothing to show for this period.</EmptyState>
        ) : (
          <table className="w-full min-w-[28rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th scope="col" className="px-4 py-2.5 font-medium">
                  Agent
                </th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">
                  Time logged
                </th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">
                  Tasks done
                </th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">
                  Tasks open
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {agents.map((a) => (
                <tr key={a.id ?? a.name} className="hover:bg-surface-2/60">
                  <th scope="row" className="px-4 py-3 text-left font-medium">
                    {a.name}
                  </th>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {a.time_minutes === null ? dash : formatMinutes(a.time_minutes)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">{count(a.tasks_done)}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{count(a.tasks_open)}</td>
                </tr>
              ))}
            </tbody>
          </table>
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
  return (
    <div className={cn("space-y-6 transition-opacity", q.isPlaceholderData && "opacity-60")}>
      <div className="flex flex-wrap items-center justify-end gap-2">
        {d.governance_url && (
          <a
            href={d.governance_url}
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
        <section aria-label="Summary" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {d.tiles.map((t) => (
            <Stat key={t.key} label={t.label} value={tileValue(t)} />
          ))}
        </section>
      )}
      <Bars title="Backlog by age" rows={d.backlog} />
      <div className="grid gap-6 lg:grid-cols-3">
        <Bars title="Channels" rows={d.channels} />
        <Bars title="Topics" rows={d.topics} />
        <Bars title="Sentiment" rows={d.sentiment} />
      </div>
      <AgentsMetrics agents={d.agents} />
    </div>
  );
}

/** "AI" tab: what the AI assistant did. No CSV: /reports/ai has none. */
export function AiPanel({ params }: { params: ReportParams }) {
  const q = useAiReport(params);
  if (q.isPending) return <Skeleton className="h-28 rounded-xl" />;
  if (q.isError) return <LoadError error={q.error} />;
  const d = q.data;
  return (
    <section
      aria-label="AI assistant"
      className={cn(
        "grid gap-3 transition-opacity sm:grid-cols-2 lg:grid-cols-4",
        q.isPlaceholderData && "opacity-60",
      )}
    >
      <Stat label="AI chats" value={count(d.chats)} />
      <Stat label="Handed to a person" value={count(d.handed_over)} />
      <Stat label="Resolved by AI" value={count(d.resolved_by_ai)} />
      <Stat
        label="CSAT of AI"
        value={d.csat_average === null ? dash : `${d.csat_average.toFixed(1)} ★`}
        hint={d.csat_count ? `${d.csat_count} ratings` : undefined}
      />
    </section>
  );
}

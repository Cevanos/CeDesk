"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Inbox, UserCheck, Users } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { Badge, Card, EmptyState, Select, Skeleton } from "@/components/ui/primitives";
import { SectionHeader } from "@/components/ui/section";
import { DataTable, RowTh, TBody, Td, Th, THead, Tr } from "@/components/ui/table";
import { LoadError } from "@/components/workspace/list-shared";
import { GetCsvButton } from "@/components/workspace/report-csv";
import { useReportFilters } from "@/components/workspace/reports-frame";
import { useAgentLiveEvents } from "@/hooks/use-live-stream";
import { useMemberOptions, useTeams } from "@/hooks/use-workspace";
import { useLiveReport } from "@/hooks/workspace/reports-explore";
import { duration, type LiveTicket } from "@/lib/reports";
import { cn } from "@/lib/utils";

const WINDOWS = [
  { value: "30", label: "30 minutes" },
  { value: "60", label: "1 hour" },
  { value: "240", label: "4 hours" },
  { value: "1440", label: "1 day" },
];
const LIVE_EVENTS = new Set([
  "ticket.created",
  "ticket.updated",
  "ticket.resolved",
  "assignment.changed",
]);
const THROTTLE_MS = 5_000;
const LIVE_KEY = ["reports", "live"] as const;

function Tile({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "danger" | "warning";
}) {
  return (
    <Card className="relative overflow-hidden p-4 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-pop">
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-0 left-0 w-1 rounded-r-full bg-border-strong",
          value > 0 && tone === "danger" && "bg-danger",
          value > 0 && tone === "warning" && "bg-warning",
          value > 0 && !tone && "bg-primary",
        )}
      />
      <p className="text-[11px] font-medium tracking-wider text-subtle-foreground uppercase">
        {label}
      </p>
      <p
        className={cn(
          "mt-0.5 text-2xl font-semibold tracking-tight tabular-nums",
          value > 0 && tone === "danger" && "text-danger",
          value > 0 && tone === "warning" && "text-warning",
        )}
      >
        {value.toLocaleString()}
      </p>
    </Card>
  );
}

const pad = (n: number) => String(n).padStart(2, "0");

/** "Live" tab: the queue right now, for a wall screen. */
export function LiveTab() {
  const { teamId } = useReportFilters();
  const [windowMinutes, setWindowMinutes] = useState("60");
  const params = { team_id: teamId || undefined, window_minutes: Number(windowMinutes) };
  const q = useLiveReport(params);
  const members = useMemberOptions();
  const teams = useTeams();
  const qc = useQueryClient();

  // Besides the 30 s timer, a ticket event refreshes it too, at most once every 5 s.
  const last = useRef(0);
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);
  useAgentLiveEvents((e) => {
    if (!LIVE_EVENTS.has(e.name) && e.name !== "reset") return;
    const refetch = () => {
      last.current = Date.now();
      pending.current = null;
      void qc.invalidateQueries({ queryKey: LIVE_KEY });
    };
    const wait = THROTTLE_MS - (Date.now() - last.current);
    if (wait <= 0) refetch();
    else if (!pending.current) pending.current = setTimeout(refetch, wait);
  });

  if (q.isPending) return <Skeleton className="h-72 rounded-xl" />;
  if (q.isError) return <LoadError error={q.error} />;
  const d = q.data;
  const memberName = (id: string | null) =>
    id ? (members.data?.find((m) => m.id === id)?.name ?? "Someone") : "Unassigned";
  const teamName = (id: string | null) =>
    id ? (teams.data?.find((t) => t.id === id)?.name ?? "A team") : "No team";
  const now = new Date();

  return (
    <div className={cn("space-y-10 transition-opacity", q.isPlaceholderData && "opacity-60")}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-40">
            <label htmlFor="live-window" className="mb-1 block text-xs text-muted-foreground">
              SLA due within
            </label>
            <Select
              id="live-window"
              value={windowMinutes}
              onChange={(e) => setWindowMinutes(e.target.value)}
              options={WINDOWS}
            />
          </div>
          <p className="pb-2 text-xs text-muted-foreground">
            Updated {new Date(d.generated_at).toLocaleTimeString()}
          </p>
        </div>
        <GetCsvButton
          path="reports/live"
          query={params}
          fallback={`cedesk-live-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}.csv`}
        />
      </div>

      <section aria-label="Queue" className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Tile label="Open" value={d.total.open} />
        <Tile label="Pending" value={d.total.pending} />
        <Tile label="Snoozed" value={d.total.snoozed} />
        <Tile label="Past SLA" value={d.total.breached} tone="danger" />
        <Tile label="SLA due soon" value={d.total.at_risk} tone="warning" />
      </section>

      <section aria-label="Teams">
        <SectionHeader icon={Users} title="By team" description="Live load per team." />
        <Card>
          <DataTable minWidth="28rem">
            <THead>
              <tr>
                <Th>Team</Th>
                {["Open", "Pending", "Snoozed", "Past SLA", "Due soon"].map((h) => (
                  <Th key={h} right>
                    {h}
                  </Th>
                ))}
              </tr>
            </THead>
            <TBody>
              {d.teams.map((t) => (
                <Tr key={t.team_id ?? "none"}>
                  <RowTh className="py-2.5">{t.name}</RowTh>
                  <Td right className="py-2.5">
                    {t.open}
                  </Td>
                  <Td right className="py-2.5">
                    {t.pending}
                  </Td>
                  <Td right className="py-2.5">
                    {t.snoozed}
                  </Td>
                  <Td right className={cn("py-2.5", t.breached > 0 && "font-medium text-danger")}>
                    {t.breached}
                  </Td>
                  <Td right className={cn("py-2.5", t.at_risk > 0 && "font-medium text-warning")}>
                    {t.at_risk}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </DataTable>
        </Card>
      </section>

      <div className="grid gap-5 lg:grid-cols-2">
        <TicketList
          title="Waiting for a first reply"
          empty="Nobody is waiting for a first reply."
          tickets={d.waiting}
          teamName={teamName}
          memberName={memberName}
          when={(t) => `waiting ${duration(t.waiting_minutes ?? 0)}`}
        />
        <TicketList
          title="SLA due soon"
          empty="Nothing is due soon."
          tickets={d.due_soon}
          teamName={teamName}
          memberName={memberName}
          when={(t) => `in ${duration(t.minutes_left ?? 0)}`}
          urgent
        />
      </div>

      <section aria-label="Agents">
        <SectionHeader icon={UserCheck} title="Agents" description="Who is available right now." />
        <div className="grid gap-3 sm:grid-cols-4">
          <Tile label="Available" value={d.agents.available} />
          <Tile label="On break" value={d.agents.on_break} />
          <Tile label="Transfers only" value={d.agents.transfers_only} />
          <Tile label="Offline" value={d.agents.offline} />
        </div>
      </section>
    </div>
  );
}

function TicketList({
  title,
  empty,
  tickets,
  teamName,
  memberName,
  when,
  urgent,
}: {
  title: string;
  empty: string;
  tickets: LiveTicket[];
  teamName: (id: string | null) => string;
  memberName: (id: string | null) => string;
  when: (t: LiveTicket) => string;
  urgent?: boolean;
}) {
  return (
    <section aria-label={title}>
      <SectionHeader icon={Inbox} title={title} />
      <Card className="h-80 divide-y divide-border overflow-y-auto">
        {tickets.length === 0 ? (
          <EmptyState title={empty} />
        ) : (
          tickets.map((t) => (
            <Link
              key={t.id}
              href={`/tickets/${t.id}`}
              className="flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-surface-2/60"
            >
              <span className="w-16 shrink-0 font-medium tabular-nums">{t.label}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate">{t.subject}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {teamName(t.team_id)} · {memberName(t.assignee_id)}
                </span>
              </span>
              <Badge tone={t.priority === "urgent" || t.priority === "high" ? "danger" : "neutral"}>
                {t.priority.charAt(0).toUpperCase() + t.priority.slice(1)}
              </Badge>
              <span
                className={cn(
                  "w-24 shrink-0 text-right text-xs tabular-nums",
                  urgent ? "text-warning" : "text-muted-foreground",
                )}
              >
                {when(t)}
              </span>
            </Link>
          ))
        )}
      </Card>
    </section>
  );
}

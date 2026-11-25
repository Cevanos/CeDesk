"use client";

import { ArrowLeft, Loader2, XCircle } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { Badge, Button, Card, EmptyState, Skeleton } from "@/components/ui/primitives";
import { DataTable, TBody, Td, Th, THead, Tr } from "@/components/ui/table";
import { LoadError } from "@/components/workspace/list-shared";
import { useAutomationAccess } from "@/hooks/use-workspace";
import { replayActive, useCancelReplay, useReplay } from "@/hooks/workspace/replays";
import { ApiError } from "@/lib/api/client";
import type { Replay, ReplayState } from "@/lib/api/differentiator-types";
import { formatDateTime } from "@/lib/utils";

const STATE: Record<
  ReplayState,
  { label: string; tone: "info" | "primary" | "success" | "danger" | "neutral" }
> = {
  queued: { label: "Queued", tone: "info" },
  running: { label: "Running", tone: "primary" },
  done: { label: "Done", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

const show = (v: unknown): string => {
  if (v === null || v === undefined || v === "") return "–";
  if (Array.isArray(v)) return v.length ? v.map(String).join(", ") : "–";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
};

export function ReplayPage({ id }: { id: string }) {
  const access = useAutomationAccess();
  const q = useReplay(id);

  if (access.isPending) return <Skeleton className="h-64" />;
  if (!access.settings) {
    return (
      <EmptyState title="You don't have access to replays">
        Replays are for Admins who manage automation.
      </EmptyState>
    );
  }
  if (q.isPending) return <Skeleton className="h-64" />;
  if (q.isError) {
    return q.error instanceof ApiError && q.error.status === 404 ? (
      <EmptyState title="Replay not found">
        Finished replays are kept for 30 days, so it may have expired.
      </EmptyState>
    ) : (
      <LoadError error={q.error} />
    );
  }
  return <Body r={q.data} />;
}

function Body({ r }: { r: Replay }) {
  const cancel = useCancelReplay(r.id);
  const state = STATE[r.state] ?? STATE.queued;
  const active = replayActive(r.state);
  const workflow = r.kind === "workflow";
  const samples = r.samples ?? [];
  const conflicts = r.conflict_samples ?? [];

  const counters = [
    { label: "Checked", value: r.checked },
    { label: "Matched", value: r.matched },
    { label: "Would change", value: r.would_change },
    ...(workflow ? [{ label: "Would wait", value: r.would_wait ?? 0 }] : []),
    { label: "Conflicts", value: r.conflicts },
  ];

  return (
    <div className="space-y-6">
      <div>
        <Link
          href={workflow ? "/settings/workflows" : "/settings/rules"}
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" /> {workflow ? "Workflows" : "Rules"}
        </Link>
        <h2 className="mt-2 text-lg font-semibold tracking-tight">
          Replay of {workflow ? "workflow" : "rule"}{" "}
          <em className="not-italic">{r.name || "(unsaved)"}</em>
          {workflow && r.target_version ? `, version ${r.target_version}` : ""}
        </h2>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <Badge tone={state.tone}>
            {active && <Loader2 className="size-3 animate-spin" aria-hidden />} {state.label}
          </Badge>
          {r.summary && <span>{r.summary}</span>}
          {active && (
            <Button
              size="sm"
              variant="outline"
              className="ml-auto"
              loading={cancel.isPending}
              onClick={() =>
                cancel.mutate(undefined, {
                  onSuccess: () => toast.success("Replay cancelled"),
                  onError: (e) =>
                    toast.error(e instanceof ApiError ? e.userMessage : "Couldn't cancel."),
                })
              }
            >
              <XCircle className="size-4" /> Cancel
            </Button>
          )}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Test only: nothing on any ticket changed and nothing was sent.
        </p>
      </div>

      {r.state === "failed" && r.error && (
        <p
          role="alert"
          className="rounded-lg border border-danger/25 bg-danger-soft p-3 text-sm text-danger"
        >
          {r.error}
        </p>
      )}
      {r.state === "cancelled" && (
        <p role="status" className="rounded-lg bg-surface-2 p-3 text-sm text-muted-foreground">
          Cancelled. What it found before stopping is shown below.
        </p>
      )}

      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5" aria-label="Totals">
        {counters.map((c) => (
          <li key={c.label}>
            <Card className="p-4">
              <p className="text-xs text-muted-foreground">{c.label}</p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">{c.value}</p>
            </Card>
          </li>
        ))}
      </ul>

      <section aria-label="What it would have done">
        <h3 className="mb-2 text-sm font-semibold">
          What it would have done{" "}
          <span className="font-normal text-muted-foreground">
            ({samples.length}
            {r.would_change > samples.length ? ` of ${r.would_change}` : ""} shown)
          </span>
        </h3>
        <Card>
          {samples.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">
              {active ? "Nothing yet." : "No ticket would have changed."}
            </p>
          ) : (
            <DataTable minWidth="34rem">
              <THead>
                <tr>
                  <Th>Ticket</Th>
                  <Th>Changes</Th>
                  <Th>Would write</Th>
                </tr>
              </THead>
              <TBody>
                {samples.map((s, i) => (
                  <Tr key={`${s.ticket_id ?? i}-${i}`} className="align-top">
                    <Td>
                      <TicketLink id={s.ticket_id} number={s.number} subject={s.subject} />
                    </Td>
                    <Td>
                      {Object.entries(s.changes ?? {}).length === 0 ? (
                        "–"
                      ) : (
                        <ul className="space-y-0.5">
                          {Object.entries(s.changes ?? {}).map(([field, c]) => (
                            <li key={field}>
                              <span className="text-muted-foreground">{field}:</span>{" "}
                              {show(c?.old ?? c?.from)} →{" "}
                              <span className="font-medium">{show(c?.new ?? c?.to)}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </Td>
                    <Td>
                      {(s.said ?? []).length === 0 ? (
                        "–"
                      ) : (
                        <ul className="space-y-0.5">
                          {(s.said ?? []).map((line, j) => (
                            <li key={j} className="whitespace-pre-wrap">
                              {line}
                            </li>
                          ))}
                        </ul>
                      )}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </DataTable>
          )}
        </Card>
      </section>

      {(r.conflicts > 0 || conflicts.length > 0) && (
        <section aria-label="Conflicts">
          <h3 className="mb-2 text-sm font-semibold">
            Conflicts with other rules{" "}
            <span className="font-normal text-muted-foreground">({conflicts.length} shown)</span>
          </h3>
          <Card>
            <DataTable minWidth="40rem">
              <THead>
                <tr>
                  <Th>Ticket</Th>
                  <Th>Field</Th>
                  <Th>This rule sets</Th>
                  <Th>Other rule</Th>
                  <Th>Winner</Th>
                </tr>
              </THead>
              <TBody>
                {conflicts.map((c, i) => (
                  <Tr key={`${c.ticket_id ?? i}-${c.field}-${i}`} className="align-top">
                    <Td>
                      <TicketLink id={c.ticket_id} number={c.number} subject={c.subject} />
                    </Td>
                    <Td>{c.field}</Td>
                    <Td>{show(c.tested_value)}</Td>
                    <Td>
                      {show(c.other_rule)} <span className="text-muted-foreground">sets</span>{" "}
                      {show(c.other_value)}
                    </Td>
                    <Td className="font-medium">{show(c.winner)}</Td>
                  </Tr>
                ))}
              </TBody>
            </DataTable>
          </Card>
        </section>
      )}

      <p className="text-xs text-muted-foreground">
        {r.requested_by_name ? `Started by ${r.requested_by_name}` : "Started"}
        {r.created_at ? ` · ${formatDateTime(r.created_at)}` : ""}
        {r.finished_at ? ` · finished ${formatDateTime(r.finished_at)}` : ""}
        {" · Kept for 30 days."}
      </p>
    </div>
  );
}

function TicketLink({
  id,
  number,
  subject,
}: {
  id?: string | null;
  number?: number | string | null;
  subject?: string | null;
}) {
  const text = (
    <>
      {number != null && (
        <span className="font-mono text-xs text-muted-foreground">#{number} </span>
      )}
      <span className="font-medium">{subject || "(no subject)"}</span>
    </>
  );
  return id ? (
    <Link href={`/tickets/${encodeURIComponent(id)}`} className="hover:underline">
      {text}
    </Link>
  ) : (
    <span>{text}</span>
  );
}

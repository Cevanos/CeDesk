"use client";

import { AlertTriangle, CheckCircle2, MinusCircle, RefreshCw, XCircle } from "lucide-react";

import { Dialog } from "@/components/ui/overlay";
import { Badge, Button, Card, Skeleton } from "@/components/ui/primitives";
import { label as sourceLabel } from "@/components/workspace/imports-sources";
import { useCutover, usePreMigration } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { CheckStatus, ReadinessStatus } from "@/lib/api/differentiator-types";
import { cn } from "@/lib/utils";

function Failure({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  // 409 closed connection, 404 unknown run, 422/503 the source refused or can't be reached.
  const retry = error instanceof ApiError && (error.status === 422 || error.status === 503);
  return (
    <div role="alert" className="space-y-3 rounded-lg bg-danger-soft p-4 text-sm text-danger">
      <p>
        {error instanceof ApiError ? error.message || error.userMessage : "We couldn't load this."}
      </p>
      {retry && onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RefreshCw className="size-3.5" /> Retry
        </Button>
      )}
    </div>
  );
}

/* ---------- Screen A: pre-migration report ---------- */

const GROUPS: { status: ReadinessStatus; title: string; tone: "success" | "warning" | "danger" }[] =
  [
    { status: "imports", title: "Will import", tone: "success" },
    { status: "needs_setup", title: "Set up in CeDesk first", tone: "warning" },
    { status: "cannot_move", title: "Can't move", tone: "danger" },
  ];

/** What would and would not come over from the source. Read only: nothing is saved. */
export function PreMigrationDialog({
  connectionId,
  onClose,
}: {
  connectionId: string;
  onClose: () => void;
}) {
  const q = usePreMigration(connectionId);
  return (
    <Dialog
      open
      onClose={onClose}
      title={q.data ? `${sourceLabel(q.data.source)}: check before import` : "Check before import"}
      description="What will come over, what needs setting up first, and what can't be moved. Nothing is imported or saved."
      className="sm:max-w-2xl"
    >
      {q.isPending ? (
        <div className="space-y-3">
          <Skeleton className="h-16" />
          <Skeleton className="h-40" />
        </div>
      ) : q.isError ? (
        <Failure error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <div className="space-y-5">
          <ul className="grid grid-cols-3 gap-3" aria-label="Totals">
            {GROUPS.map((g) => (
              <li key={g.status}>
                <Card className="p-3 text-center">
                  <p className="text-2xl font-semibold tabular-nums">
                    {q.data.totals[g.status] ?? 0}
                  </p>
                  <p className="text-xs text-muted-foreground">{g.title}</p>
                </Card>
              </li>
            ))}
          </ul>

          {q.data.warnings.length > 0 && (
            <ul
              role="note"
              className="space-y-1 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning"
            >
              {q.data.warnings.map((w) => (
                <li key={w} className="flex gap-2">
                  <AlertTriangle className="mt-0.5 size-3.5 flex-none" aria-hidden />
                  {w}
                </li>
              ))}
            </ul>
          )}

          {GROUPS.map((g) => {
            const items = q.data.items.filter((i) => i.status === g.status);
            if (items.length === 0) return null;
            return (
              <section key={g.status} aria-label={g.title}>
                <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                  {g.title} <Badge tone={g.tone}>{items.length}</Badge>
                </h3>
                <Card className="divide-y divide-border">
                  {items.map((i) => (
                    <div key={i.key} className="flex items-start gap-3 px-4 py-3">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium">{i.name}</p>
                        {i.note && <p className="mt-0.5 text-xs text-muted-foreground">{i.note}</p>}
                      </div>
                      {i.count != null && (
                        <span className="text-sm font-semibold tabular-nums">
                          {i.count.toLocaleString()}
                        </span>
                      )}
                    </div>
                  ))}
                </Card>
              </section>
            );
          })}
        </div>
      )}
      <div className="mt-5 flex justify-end">
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
    </Dialog>
  );
}

/* ---------- Screen B: go-live checklist ---------- */

const CHECK_LABEL: Record<string, string> = {
  import_finished: "Import finished",
  counts_match: "Counts match",
  no_open_failures: "No open failures",
  sync_fresh: "Sync is fresh",
  agents_matched: "Agents matched",
  teams_set: "Teams set",
  email_ready: "Email ready",
};
const ORDER = Object.keys(CHECK_LABEL);
const labelOf = (key: string) =>
  CHECK_LABEL[key] ?? key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

const ICON: Record<CheckStatus, { Icon: typeof CheckCircle2; cls: string; text: string }> = {
  ok: { Icon: CheckCircle2, cls: "text-success", text: "OK" },
  problem: { Icon: XCircle, cls: "text-danger", text: "Problem" },
  skipped: { Icon: MinusCircle, cls: "text-subtle-foreground", text: "Skipped" },
};

/** Is this import safe to go live with? Seven checks, each with what to do. */
export function CutoverDialog({ runId, onClose }: { runId: string; onClose: () => void }) {
  const q = useCutover(runId);
  const checks = q.data
    ? [...q.data.checks].sort((a, b) => ORDER.indexOf(a.key) - ORDER.indexOf(b.key))
    : [];
  return (
    <Dialog
      open
      onClose={onClose}
      title="Go-live checklist"
      description={
        q.data
          ? `${sourceLabel(q.data.source)} import`
          : "Is this import ready for your team to switch over?"
      }
      className="sm:max-w-xl"
    >
      {q.isPending ? (
        <Skeleton className="h-64" />
      ) : q.isError ? (
        <Failure error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <div className="space-y-4">
          <p
            role="status"
            className={cn(
              "inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-semibold",
              q.data.ready ? "bg-success-soft text-success" : "bg-warning-soft text-warning",
            )}
          >
            {q.data.ready ? (
              <>
                <CheckCircle2 className="size-4" aria-hidden /> Ready to go live
              </>
            ) : (
              <>
                <AlertTriangle className="size-4" aria-hidden /> Not ready yet
              </>
            )}
          </p>
          <ul className="divide-y divide-border rounded-xl border border-border">
            {checks.map((c) => {
              const { Icon, cls, text } = ICON[c.status] ?? ICON.skipped;
              return (
                <li key={c.key} className="flex items-start gap-3 px-4 py-3">
                  <Icon className={cn("mt-0.5 size-4 flex-none", cls)} aria-hidden />
                  <span className="sr-only">{text}:</span>
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{labelOf(c.key)}</p>
                    {c.detail && <p className="mt-0.5 text-xs text-muted-foreground">{c.detail}</p>}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      <div className="mt-5 flex justify-end gap-2">
        {q.data && (
          <Button variant="outline" loading={q.isFetching} onClick={() => void q.refetch()}>
            <RefreshCw className="size-4" /> Check again
          </Button>
        )}
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
    </Dialog>
  );
}

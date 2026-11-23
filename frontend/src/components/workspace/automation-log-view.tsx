"use client";

import { Workflow, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { Drawer } from "@/components/ui/drawer";
import { InfiniteScroll } from "@/components/ui/infinite-scroll";
import { Badge, Button, Card, EmptyState, Select, Skeleton } from "@/components/ui/primitives";
import { formatWhen, outcomeLabel, TicketPicker } from "@/components/workspace/automation-test";
import { LoadError } from "@/components/workspace/list-shared";
import { EVENT_LABEL } from "@/components/workspace/rules-view";
import {
  useAutomationCatalog,
  useAutomationRuns,
  useCancelRun,
  useFlows,
  useRules,
} from "@/hooks/use-workspace";
import type { AutomationRun, RunFilters, RunState } from "@/lib/api/automation-types";
import { ApiError } from "@/lib/api/client";
import type { TicketSummary } from "@/lib/api/types";

const STATE_TONE: Record<RunState, "success" | "info" | "warning" | "neutral" | "danger"> = {
  done: "success",
  waiting: "info",
  blocked: "warning",
  cancelled: "neutral",
  failed: "danger",
};
const STATE_LABEL: Record<RunState, string> = {
  done: "Done",
  waiting: "Waiting",
  blocked: "Blocked",
  cancelled: "Cancelled",
  failed: "Failed",
};
const STATE_OPTIONS = [
  { value: "all", label: "Any state" },
  ...(Object.keys(STATE_LABEL) as RunState[]).map((s) => ({ value: s, label: STATE_LABEL[s] })),
];

const KEYS = ["rule_id", "flow_id", "ticket_id", "state", "trigger"] as const;

/** Filters live in the URL, so a rule's or ticket's "Run log" link arrives pre-filtered. */
function useRunFilters() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const filters: RunFilters = {};
  for (const k of KEYS) {
    const v = params.get(k);
    if (v) filters[k] = v;
  }
  const set = (patch: Partial<Record<(typeof KEYS)[number], string | undefined>>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    const q = next.toString();
    router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false });
  };
  return { filters, set };
}

export function AutomationLogView() {
  const { filters, set } = useRunFilters();
  const runs = useAutomationRuns(filters);
  const catalog = useAutomationCatalog();
  const rules = useRules();
  const flows = useFlows();
  const [open, setOpen] = useState<AutomationRun | null>(null);
  const [pickTicket, setPickTicket] = useState(false);

  const triggerLabel = useMemo(() => {
    const map = new Map(catalog.data?.triggers.map((t) => [t.event, t.label]));
    return (t: string) => map.get(t) ?? EVENT_LABEL[t as keyof typeof EVENT_LABEL] ?? t;
  }, [catalog.data]);

  const triggerOptions = [
    { value: "all", label: "Any trigger" },
    ...(catalog.data?.triggers ?? []).map((t) => ({ value: t.event, label: t.label })),
  ];
  const ruleOptions = [
    { value: "all", label: "Any rule" },
    ...(rules.data ?? []).map((r) => ({ value: r.id, label: r.name })),
  ];
  const flowOptions = [
    { value: "all", label: "Any workflow" },
    ...(flows.data ?? []).map((f) => ({ value: f.id, label: f.name })),
  ];
  const all = runs.data?.pages.flatMap((p) => p.runs) ?? [];

  return (
    <div>
      <p className="mb-4 text-sm text-muted-foreground">
        Runs are kept for 30 days. Message text is never kept here.
      </p>

      <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <Select
          aria-label="Filter by state"
          value={filters.state ?? "all"}
          options={STATE_OPTIONS}
          onChange={(e) => set({ state: e.target.value === "all" ? undefined : e.target.value })}
        />
        <Select
          aria-label="Filter by trigger"
          value={filters.trigger ?? "all"}
          options={triggerOptions}
          onChange={(e) => set({ trigger: e.target.value === "all" ? undefined : e.target.value })}
        />
        <Select
          aria-label="Filter by rule"
          value={filters.rule_id ?? "all"}
          options={ruleOptions}
          onChange={(e) => set({ rule_id: e.target.value === "all" ? undefined : e.target.value })}
        />
        <Select
          aria-label="Filter by workflow"
          value={filters.flow_id ?? "all"}
          options={flowOptions}
          onChange={(e) => set({ flow_id: e.target.value === "all" ? undefined : e.target.value })}
        />
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {filters.ticket_id ? (
          <Badge tone="primary" className="gap-1.5 py-1 text-xs">
            One ticket only
            <button
              type="button"
              aria-label="Remove the ticket filter"
              onClick={() => set({ ticket_id: undefined })}
            >
              <X className="size-3" />
            </button>
          </Badge>
        ) : (
          <Button size="sm" variant="outline" onClick={() => setPickTicket((o) => !o)}>
            Filter by ticket
          </Button>
        )}
      </div>
      {pickTicket && !filters.ticket_id && (
        <div className="mb-4 max-w-md">
          <TicketPicker
            value={null}
            onPick={(t: TicketSummary | null) => {
              if (t) {
                set({ ticket_id: t.id });
                setPickTicket(false);
              }
            }}
          />
        </div>
      )}

      {runs.isPending ? (
        <Skeleton className="h-40" />
      ) : runs.isError ? (
        <LoadError error={runs.error} />
      ) : all.length === 0 ? (
        <Card>
          <EmptyState icon={<Workflow className="size-6" />} title="No runs">
            Nothing matches these filters in the last 30 days.
          </EmptyState>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-border">
            {all.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => setOpen(r)}
                  className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left hover:bg-surface-2"
                >
                  <span className="w-28 flex-none text-xs text-muted-foreground">
                    {formatWhen(r.created_at)}
                  </span>
                  <span className="min-w-0 flex-1 basis-48">
                    <span className="flex items-center gap-2">
                      <Badge tone="neutral">{r.kind === "flow" ? "Workflow" : "Rule"}</Badge>
                      <span className="truncate font-medium">{r.name}</span>
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">
                      #{r.ticket_number} · {triggerLabel(r.trigger)}
                      {r.result ? ` · ${r.result}` : ""}
                    </span>
                  </span>
                  <Badge tone={STATE_TONE[r.state]}>{STATE_LABEL[r.state]}</Badge>
                </button>
              </li>
            ))}
          </ul>
          <InfiniteScroll
            query={runs}
            shown={all.length}
            noun="runs"
            className="border-t border-border"
          />
        </Card>
      )}

      {open && (
        <RunDetail
          run={open}
          triggerLabel={triggerLabel}
          onClose={() => setOpen(null)}
          onChange={setOpen}
        />
      )}
    </div>
  );
}

function RunDetail({
  run,
  triggerLabel,
  onClose,
  onChange,
}: {
  run: AutomationRun;
  triggerLabel: (t: string) => string;
  onClose: () => void;
  onChange: (r: AutomationRun) => void;
}) {
  const cancel = useCancelRun();
  const { ask, dialog } = useConfirm();
  const [error, setError] = useState<string | null>(null);

  return (
    <Drawer
      open
      onClose={onClose}
      title={run.name}
      description={`${run.kind === "flow" ? "Workflow" : "Rule"} run · ${formatWhen(run.created_at)}`}
    >
      {dialog}
      <div className="space-y-4 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={STATE_TONE[run.state]}>{STATE_LABEL[run.state]}</Badge>
          <Link
            href={`/tickets/${run.ticket_id}`}
            className="font-medium text-primary-ink hover:underline"
          >
            Ticket #{run.ticket_number}
          </Link>
          <span className="text-muted-foreground">{triggerLabel(run.trigger)}</span>
        </div>
        {run.result && <p>{run.result}</p>}
        {run.error && (
          <p
            role="alert"
            className="rounded-lg border border-danger/25 bg-danger-soft px-3 py-2 text-danger"
          >
            {run.error}
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          Loop depth {run.depth}
          {run.flow_version ? ` · Version ${run.flow_version}` : ""}
        </p>

        {run.state === "waiting" && run.resume_at && (
          <div className="space-y-2 rounded-lg bg-info-soft px-3 py-2 text-info">
            <p>
              Goes on at the latest {formatWhen(run.resume_at)}
              {run.until_reply ? ", or when the customer replies" : ""}.
            </p>
            <Button
              size="sm"
              variant="outline"
              loading={cancel.isPending}
              onClick={() =>
                ask({
                  title: "Stop this run?",
                  message: "It will not go on. This can't be undone.",
                  detail: run.name,
                  confirmLabel: "Stop this run",
                  run: () =>
                    cancel.mutateAsync(run.id).then(
                      (r) => {
                        toast.success("Run stopped");
                        onChange(r);
                      },
                      (e) => setError(e instanceof ApiError ? e.userMessage : "Could not stop it."),
                    ),
                })
              }
            >
              Stop this run
            </Button>
            {error && (
              <p role="alert" className="text-danger">
                {error}
              </p>
            )}
          </div>
        )}

        <section aria-label="Steps">
          <h3 className="mb-1 text-[13px] font-semibold">Steps</h3>
          {run.steps.length === 0 ? (
            <p className="text-muted-foreground">No steps were recorded.</p>
          ) : (
            <ol className="space-y-1.5">
              {run.steps.map((s, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2">
                  {s.node && <span className="font-medium">{s.node}</span>}
                  <Badge tone={s.outcome === "waiting" ? "info" : "neutral"}>
                    {outcomeLabel(s.outcome)}
                  </Badge>
                  {s.said && <span className="text-muted-foreground">{s.said}</span>}
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </Drawer>
  );
}

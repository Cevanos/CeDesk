"use client";

import {
  AlarmClock,
  ChevronDown,
  Flag,
  ListChecks,
  Tag,
  UserRound,
  UsersRound,
  X,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { PRIORITIES, PRIORITY_LABEL, STATUSES, STATUS_LABEL } from "@/components/tickets/badges";
import { SnoozeMenu } from "@/components/tickets/inbox/snooze";
import { Dialog, MenuItem, Popover } from "@/components/ui/overlay";
import { Button, Input } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { BULK_MAX, useBulkTickets } from "@/hooks/use-inbox-tools";
import { useAgents, useTeams } from "@/hooks/use-workspace";
import type { BulkAction, BulkResult, BulkResultItem } from "@/lib/api/types";

/** "Changed 50 tickets" or "Changed 47 of 50 tickets". */
export function bulkSummary(r: Pick<BulkResult, "succeeded" | "failed">): string {
  const total = r.succeeded + r.failed;
  const noun = (n: number) => (n === 1 ? "ticket" : "tickets");
  return r.failed === 0
    ? `Changed ${r.succeeded} ${noun(r.succeeded)}`
    : `Changed ${r.succeeded} of ${total} ${noun(total)}`;
}

type TriggerProps = { props: React.ButtonHTMLAttributes<HTMLButtonElement> };

/**
 * The bar above the list while tickets are selected. One button per change; each sends one
 * request for every selected ticket. `labels` turns ticket ids into T-numbers for the problems list.
 */
export function BulkBar({
  ids,
  labels,
  onClear,
  onDone,
}: {
  ids: string[];
  labels: Record<string, string>;
  onClear: () => void;
  /** Called with the ids that failed, so the list can keep only those selected. */
  onDone: (failedIds: string[]) => void;
}) {
  const { user, perms } = useCurrentUser();
  const bulk = useBulkTickets();
  const agents = useAgents();
  const teams = useTeams();
  const [problems, setProblems] = useState<BulkResultItem[] | null>(null);
  const tooMany = ids.length > BULK_MAX;
  const busy = bulk.isPending;

  const run = (change: BulkAction) =>
    bulk.mutate(
      { ticket_ids: ids, ...change },
      {
        onSuccess: (r) => {
          const bad = r.results.filter((x) => !x.ok);
          if (bad.length === 0) toast.success(bulkSummary(r));
          else {
            toast.warning(bulkSummary(r), {
              action: { label: `Show ${bad.length} problems`, onClick: () => setProblems(bad) },
              duration: 10_000,
            });
          }
          onDone(bad.map((x) => x.ticket_id));
        },
      },
    );

  // Supervisors and admins may assign anyone; agents only themselves (the server checks too).
  const assignable = (agents.data ?? []).filter(
    (a) => perms.assignOthers || a.member_id === user?.id,
  );

  const trigger = (icon: React.ReactNode, label: string) =>
    function Trigger({ props }: TriggerProps) {
      return (
        <Button variant="outline" size="sm" disabled={tooMany || busy} {...props}>
          {icon} {label} <ChevronDown className="size-3.5 opacity-60" aria-hidden />
        </Button>
      );
    };

  return (
    <div
      role="region"
      aria-label="Bulk actions"
      className="sticky top-2 z-20 mt-4 flex flex-wrap items-center gap-2 rounded-xl border border-primary/30 bg-surface p-2.5 shadow-pop"
    >
      <span
        className="inline-flex items-center gap-1.5 px-1.5 text-sm font-semibold"
        aria-live="polite"
      >
        <ListChecks className="size-4 text-primary" aria-hidden /> {ids.length} selected
      </span>

      <Popover
        align="left"
        className="max-h-72 w-60 overflow-y-auto"
        trigger={trigger(<UserRound className="size-4" aria-hidden />, "Assign")}
      >
        {(close) => (
          <>
            <MenuItem
              onClick={() => {
                close();
                run({ action: "assign", assignee_id: null });
              }}
            >
              Unassigned
            </MenuItem>
            {assignable.map((a) => (
              <MenuItem
                key={a.member_id}
                onClick={() => {
                  close();
                  run({ action: "assign", assignee_id: a.member_id });
                }}
              >
                {a.name}
              </MenuItem>
            ))}
          </>
        )}
      </Popover>

      <Popover
        align="left"
        className="max-h-72 w-60 overflow-y-auto"
        trigger={trigger(<UsersRound className="size-4" aria-hidden />, "Team")}
      >
        {(close) =>
          (teams.data ?? []).length === 0 ? (
            <p className="px-2.5 py-2 text-sm text-muted-foreground">No teams yet.</p>
          ) : (
            (teams.data ?? []).map((t) => (
              <MenuItem
                key={t.id}
                onClick={() => {
                  close();
                  run({ action: "set_team", team_id: t.id });
                }}
              >
                {t.name}
              </MenuItem>
            ))
          )
        }
      </Popover>

      <Popover align="left" className="w-44" trigger={trigger(null, "Status")}>
        {(close) =>
          STATUSES.map((s) => (
            <MenuItem
              key={s}
              onClick={() => {
                close();
                run({ action: "set_status", status: s });
              }}
            >
              {STATUS_LABEL[s]}
            </MenuItem>
          ))
        }
      </Popover>

      <Popover
        align="left"
        className="w-44"
        trigger={trigger(<Flag className="size-4" aria-hidden />, "Priority")}
      >
        {(close) =>
          PRIORITIES.map((p) => (
            <MenuItem
              key={p}
              onClick={() => {
                close();
                run({ action: "set_priority", priority: p });
              }}
            >
              {PRIORITY_LABEL[p]}
            </MenuItem>
          ))
        }
      </Popover>

      <Popover
        align="left"
        className="w-64"
        trigger={trigger(<Tag className="size-4" aria-hidden />, "Tags")}
      >
        {(close) => (
          <TagsForm
            onRun={(action, tags) => {
              close();
              run({ action, tags });
            }}
          />
        )}
      </Popover>

      <SnoozeMenu
        align="left"
        pending={busy}
        onPick={(until) => run({ action: "snooze", until })}
        trigger={({ props }) =>
          trigger(<AlarmClock className="size-4" aria-hidden />, "Snooze")({ props })
        }
      />
      <Button
        variant="outline"
        size="sm"
        disabled={tooMany || busy}
        onClick={() => run({ action: "unsnooze" })}
      >
        Unsnooze
      </Button>

      {tooMany && (
        <span role="alert" className="text-xs font-medium text-danger">
          Select at most {BULK_MAX} tickets
        </span>
      )}

      <button
        onClick={onClear}
        aria-label="Clear selection"
        className="ml-auto grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-surface-2"
      >
        <X className="size-4" />
      </button>

      <Dialog
        open={!!problems}
        onClose={() => setProblems(null)}
        title={`${problems?.length ?? 0} ${problems?.length === 1 ? "problem" : "problems"}`}
        description="These tickets were not changed. The others were."
      >
        <ul className="space-y-2">
          {problems?.map((p) => (
            <li
              key={p.ticket_id}
              className="rounded-lg border border-border bg-surface-2/50 p-2.5 text-sm"
            >
              <span className="font-mono text-xs font-semibold">
                {labels[p.ticket_id] ?? p.ticket_id}
              </span>
              <p className="text-muted-foreground">{p.error?.message ?? "Not changed."}</p>
            </li>
          ))}
        </ul>
        <div className="mt-4 flex justify-end">
          <Button variant="outline" onClick={() => setProblems(null)}>
            Close
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

function TagsForm({
  onRun,
}: {
  onRun: (action: "add_tags" | "remove_tags", tags: string[]) => void;
}) {
  const [value, setValue] = useState("");
  // Commas or spaces split tags; the ticket page lower-cases them, so do the same here.
  const tags = value
    .split(/[,\s]+/)
    .map((t) => t.trim().toLowerCase())
    .filter(Boolean);
  return (
    <form className="space-y-2 p-2" onSubmit={(e) => e.preventDefault()}>
      <Input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="refund, vip"
        aria-label="Tags, separated by commas"
      />
      <div className="flex gap-2">
        <Button
          size="sm"
          className="flex-1"
          disabled={tags.length === 0}
          onClick={() => onRun("add_tags", tags)}
        >
          Add tags
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="flex-1"
          disabled={tags.length === 0}
          onClick={() => onRun("remove_tags", tags)}
        >
          Remove tags
        </Button>
      </div>
    </form>
  );
}

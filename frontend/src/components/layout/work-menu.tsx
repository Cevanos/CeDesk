"use client";

import { Check, ClipboardCheck, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Popover } from "@/components/ui/overlay";
import { Skeleton } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import {
  useDecideApproval,
  useMyTasks,
  useToggleTask,
  useWaitingApprovals,
  type MyTask,
  type WaitingApproval,
} from "@/hooks/use-work";
import { hasAction } from "@/lib/permissions";
import { cn, formatDateTime, timeAgo } from "@/lib/utils";

const ticketHref = (id?: string | null) => (id ? `/tickets/${encodeURIComponent(id)}` : null);

function Approval({ a, onGo }: { a: WaitingApproval; onGo: () => void }) {
  const decide = useDecideApproval();
  const href = ticketHref(a.ticket_id);
  const title = a.title ?? a.subject ?? a.ticket_subject ?? "Approval request";
  const by = a.requested_by_name ?? a.requester_name;
  const act = (decision: "approve" | "reject") =>
    decide.mutate(
      { id: a.id, decision },
      { onSuccess: () => toast.success(decision === "approve" ? "Approved" : "Rejected") },
    );
  return (
    <li className="rounded-lg px-2.5 py-2 hover:bg-surface-2/60">
      <div className="min-w-0 text-sm">
        {href ? (
          <Link href={href} onClick={onGo} className="font-medium hover:underline">
            {a.ticket_label ? `${a.ticket_label} · ` : ""}
            {title}
          </Link>
        ) : (
          <span className="font-medium">{title}</span>
        )}
      </div>
      {(a.reason ?? a.note) && (
        <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{a.reason ?? a.note}</p>
      )}
      <div className="mt-1.5 flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[11px] text-subtle-foreground">
          {by ? `${by} · ` : ""}
          {timeAgo(a.created_at)}
        </span>
        <button
          disabled={decide.isPending}
          onClick={() => act("reject")}
          className="inline-flex h-7 items-center gap-1 rounded-md border border-border px-2 text-xs font-medium text-danger hover:bg-danger-soft disabled:opacity-50"
        >
          <X className="size-3.5" /> Reject
        </button>
        <button
          disabled={decide.isPending}
          onClick={() => act("approve")}
          className="inline-flex h-7 items-center gap-1 rounded-md bg-primary px-2 text-xs font-medium text-primary-foreground hover:bg-primary-hover disabled:opacity-50"
        >
          <Check className="size-3.5" /> Approve
        </button>
      </div>
    </li>
  );
}

function Task({ t, onGo }: { t: MyTask; onGo: () => void }) {
  const toggle = useToggleTask();
  const href = ticketHref(t.ticket_id);
  const [now] = useState(() => Date.now());
  const overdue = !!t.due_at && new Date(t.due_at).valueOf() < now;
  const label = t.title ?? t.text ?? "Task";
  // Optimistic enough: the row disappears once the list refetches.
  const done = toggle.isSuccess || t.done === true;
  return (
    <li className="flex items-start gap-2.5 rounded-lg px-2.5 py-2 hover:bg-surface-2/60">
      <input
        type="checkbox"
        checked={done}
        disabled={toggle.isPending}
        onChange={(e) => toggle.mutate({ id: t.id, done: e.target.checked })}
        aria-label={`Mark done: ${label}`}
        className="mt-0.5 size-4 flex-none accent-[var(--primary)]"
      />
      <div className="min-w-0 flex-1 text-sm">
        <span className={cn(done && "text-muted-foreground line-through")}>{label}</span>
        <div className="flex flex-wrap gap-x-2 text-[11px] text-subtle-foreground">
          {t.due_at && (
            <span className={cn(overdue && !done && "font-medium text-danger")}>
              {overdue ? "Overdue · " : "Due "}
              {formatDateTime(t.due_at)}
            </span>
          )}
          {href && (
            <Link href={href} onClick={onGo} className="hover:underline">
              {t.ticket_label ?? "Open ticket"}
            </Link>
          )}
        </div>
      </div>
    </li>
  );
}

/** Top bar: requests waiting for my decision and my open tasks. */
export function WorkMenu() {
  const { user } = useCurrentUser();
  const canApprove = hasAction(
    user,
    "approve",
    user?.role === "supervisor" || user?.role === "admin",
  );
  const approvals = useWaitingApprovals(!!user && canApprove);
  const tasks = useMyTasks();
  const [tab, setTab] = useState<"approvals" | "tasks">("tasks");

  const nApprovals = canApprove ? (approvals.data?.length ?? 0) : 0;
  const nTasks = tasks.data?.length ?? 0;
  const total = nApprovals + nTasks;
  const active = !canApprove ? "tasks" : tab;

  return (
    <Popover
      className="w-[min(24rem,calc(100vw-2rem))] p-0 max-sm:fixed max-sm:inset-x-3 max-sm:top-16 max-sm:w-auto"
      trigger={({ props }) => (
        <button
          {...props}
          aria-label={`My work${total ? `, ${total} open` : ""}`}
          className="relative grid size-9 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"
        >
          <ClipboardCheck className="size-[18px]" />
          {total > 0 && (
            <span className="absolute -top-0.5 -right-0.5 grid min-w-[18px] place-items-center rounded-full bg-primary px-1 text-[10px] font-bold text-primary-foreground ring-2 ring-surface">
              {total > 99 ? "99+" : total}
            </span>
          )}
        </button>
      )}
    >
      {(close) => (
        <div>
          <div className="flex gap-1 border-b border-border px-2 pt-2" role="tablist">
            {canApprove && (
              <TabButton
                selected={active === "approvals"}
                onClick={() => setTab("approvals")}
                label="Waiting for my approval"
                count={nApprovals}
              />
            )}
            <TabButton
              selected={active === "tasks"}
              onClick={() => setTab("tasks")}
              label="My tasks"
              count={nTasks}
            />
          </div>
          <div className="max-h-96 overflow-y-auto p-1.5" role="tabpanel">
            {active === "approvals" ? (
              approvals.isLoading ? (
                <Loading />
              ) : approvals.isError ? (
                <Empty text="Couldn't load approvals." />
              ) : !approvals.data?.length ? (
                <Empty text="Nothing is waiting for your approval." />
              ) : (
                <ul>
                  {approvals.data.map((a) => (
                    <Approval key={a.id} a={a} onGo={close} />
                  ))}
                </ul>
              )
            ) : tasks.isLoading ? (
              <Loading />
            ) : tasks.isError ? (
              <Empty text="Couldn't load your tasks." />
            ) : !tasks.data?.length ? (
              <Empty text="No open tasks. Nice." />
            ) : (
              <ul>
                {tasks.data.map((t) => (
                  <Task key={t.id} t={t} onGo={close} />
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </Popover>
  );
}

function TabButton({
  selected,
  onClick,
  label,
  count,
}: {
  selected: boolean;
  onClick: () => void;
  label: string;
  count: number;
}) {
  return (
    <button
      role="tab"
      aria-selected={selected}
      onClick={onClick}
      className={cn(
        "-mb-px flex-1 border-b-2 px-2 py-2 text-[13px] font-medium whitespace-nowrap transition-colors",
        selected
          ? "border-primary text-primary-ink"
          : "border-transparent text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
      {count > 0 && <span className="ml-1.5 text-xs tabular-nums">({count})</span>}
    </button>
  );
}

const Loading = () => (
  <div className="space-y-2 p-2">
    <Skeleton className="h-10" />
    <Skeleton className="h-10" />
  </div>
);
const Empty = ({ text }: { text: string }) => (
  <p className="px-3 py-8 text-center text-sm text-muted-foreground">{text}</p>
);

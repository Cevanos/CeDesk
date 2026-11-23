import { AlarmClock, CircleCheck, CircleDot, Clock } from "lucide-react";

import { STATUSES, STATUS_LABEL } from "@/components/tickets/badges";
import { Skeleton } from "@/components/ui/primitives";
import type { TicketList, TicketStatus } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/** The four tabs: the three statuses, and snoozed tickets (a filter, not a status). */
export type ListTab = TicketStatus | "snoozed";
const TABS: ListTab[] = [...STATUSES, "snoozed"];

const STATUS_META: Record<
  ListTab,
  { icon: typeof CircleDot; accent: string; hint: string; label: string }
> = {
  open: {
    icon: CircleDot,
    accent: "text-primary-ink bg-primary-soft",
    hint: "Needs attention",
    label: STATUS_LABEL.open,
  },
  pending: {
    icon: Clock,
    accent: "text-warning bg-warning-soft",
    hint: "Waiting on someone",
    label: STATUS_LABEL.pending,
  },
  resolved: {
    icon: CircleCheck,
    accent: "text-success bg-success-soft",
    hint: "All done",
    label: STATUS_LABEL.resolved,
  },
  snoozed: {
    icon: AlarmClock,
    accent: "text-info bg-info-soft",
    hint: "Hidden until later",
    label: "Snoozed",
  },
};

export function StatusTabs({
  active,
  counts,
  loading,
  onChange,
}: {
  active: ListTab;
  counts: TicketList["counts"];
  loading: boolean;
  onChange: (tab: ListTab) => void;
}) {
  return (
    <div role="tablist" aria-label="Ticket status" className="mt-5 grid grid-cols-4 gap-2 sm:gap-3">
      {TABS.map((s) => {
        const meta = STATUS_META[s];
        const isActive = active === s;
        return (
          <button
            key={s}
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(s)}
            className={cn(
              "group relative flex min-w-0 flex-col gap-1 overflow-hidden rounded-xl border p-3 text-left transition-all sm:p-4",
              isActive
                ? "border-primary/40 bg-surface shadow-pop ring-1 ring-primary/25"
                : "border-border bg-surface/60 hover:border-border-strong hover:bg-surface hover:shadow-card",
            )}
          >
            {isActive && (
              <span
                aria-hidden
                className="absolute inset-x-0 top-0 h-0.5 bg-gradient-to-r from-primary to-primary/30"
              />
            )}
            <div className="flex items-center justify-between gap-2">
              <span
                className={cn("hidden size-8 place-items-center rounded-lg sm:grid", meta.accent)}
              >
                <meta.icon className="size-4" aria-hidden />
              </span>
              <span className="text-[12px] font-medium text-muted-foreground sm:order-first sm:hidden">
                {meta.label}
              </span>
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl leading-none font-semibold tracking-tight tabular-nums sm:text-[28px]">
                {loading && counts[s] === undefined ? (
                  <Skeleton className="inline-block h-6 w-8 align-middle" />
                ) : (
                  (counts[s] ?? "–")
                )}
              </span>
            </div>
            <div className="min-w-0">
              <div
                className={cn(
                  "hidden text-sm font-medium sm:block",
                  isActive ? "text-foreground" : "text-foreground-2",
                )}
              >
                {meta.label}
              </div>
              <div className="hidden truncate text-xs text-muted-foreground sm:block">
                {meta.hint}
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
}

import { CheckCircle2, Clock, PauseCircle } from "lucide-react";

import { Badge } from "@/components/ui/primitives";
import type { TicketSla } from "@/lib/api/types";
import { formatDateTime } from "@/lib/utils";

/** "2h 10m", "35m", "3d 4h": business minutes, absolute value. */
export function formatMinutes(total: number): string {
  const m = Math.round(Math.abs(total));
  if (m < 1) return "under 1m";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d}d ${h % 24}h` : `${d}d`;
}

const LABEL = { first_reply: "First reply", resolve: "Resolution" } as const;

/**
 * SLA pill for a ticket (guide 5.14). `on_track` grey, `close` amber,
 * `breached` red "Overdue ...", `met` a tick (hidden in the compact list view).
 */
export function SlaPill({
  sla,
  compact = false,
}: {
  sla?: TicketSla | null;
  /** List rows: hide `met` and the clock label to save space. */
  compact?: boolean;
}) {
  if (!sla) return null;
  const clock = sla[sla.current];
  const left = clock?.minutes_left;
  const what = LABEL[sla.current];

  if (sla.paused) {
    return (
      <Badge tone="neutral" title={`${what} timer is paused (outside business hours or holiday)`}>
        <PauseCircle className="size-3" aria-hidden /> Paused
      </Badge>
    );
  }
  if (sla.state === "met") {
    if (compact) return null;
    return (
      <Badge tone="success" title={`${what} met`}>
        <CheckCircle2 className="size-3" aria-hidden /> {what} met
      </Badge>
    );
  }
  if (left === undefined) return null;

  const breached = sla.state === "breached" || left < 0;
  const tone = breached ? "danger" : sla.state === "close" ? "warning" : "neutral";
  const text = breached ? `Overdue ${formatMinutes(left)}` : `${formatMinutes(left)} left`;
  return (
    <Badge
      tone={tone}
      title={`${what}: ${text}`}
      className={breached ? "ring-1 ring-danger/30" : undefined}
    >
      <Clock className="size-3" aria-hidden />
      {compact ? text : `${what}: ${text}`}
    </Badge>
  );
}

const BAR = {
  on_track: "bg-primary",
  close: "bg-warning",
  breached: "bg-danger",
  met: "bg-success",
} as const;

function ClockRow({
  label,
  clock,
  paused,
}: {
  label: string;
  clock: TicketSla["first_reply"];
  paused: boolean;
}) {
  const breached = clock.state === "breached" || (clock.state !== "met" && clock.minutes_left < 0);
  const state = breached ? "breached" : clock.state;
  const used =
    clock.state === "met"
      ? 1
      : Math.min(1, Math.max(0, 1 - clock.minutes_left / Math.max(1, clock.target_minutes)));
  const text =
    clock.state === "met"
      ? "Met"
      : breached
        ? `Overdue ${formatMinutes(clock.minutes_left)}`
        : `${formatMinutes(clock.minutes_left)} left`;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span className="font-medium text-foreground-2">{label}</span>
        <span
          className={
            breached
              ? "font-semibold text-danger"
              : state === "close"
                ? "font-semibold text-warning"
                : "text-muted-foreground"
          }
        >
          {paused && clock.state !== "met" ? "Paused" : text}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={`${label} time used`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(used * 100)}
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-3"
      >
        <div
          className={`h-full rounded-full transition-[width] ${BAR[state]} ${paused ? "opacity-40" : ""}`}
          style={{ width: `${Math.max(used * 100, 3)}%` }}
        />
      </div>
      <p className="mt-1 text-[11px] text-subtle-foreground">
        Target {formatMinutes(clock.target_minutes)}
        {clock.state !== "met" && ` · due ${formatDateTime(clock.due_at)}`}
      </p>
    </div>
  );
}

/** Sidebar panel: both clocks with a progress bar each (guide 5.14). */
export function SlaPanel({ sla }: { sla?: TicketSla | null }) {
  if (!sla) {
    return <p className="text-sm text-muted-foreground">No SLA applies to this ticket.</p>;
  }
  return (
    <div className="space-y-4">
      <ClockRow label="First reply" clock={sla.first_reply} paused={sla.paused} />
      <ClockRow label="Resolution" clock={sla.resolve} paused={sla.paused} />
      {sla.paused && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <PauseCircle className="size-3.5" aria-hidden /> Paused outside business hours or on a
          holiday.
        </p>
      )}
      {sla.policy && <p className="text-xs text-muted-foreground">Policy: {sla.policy}</p>}
    </div>
  );
}

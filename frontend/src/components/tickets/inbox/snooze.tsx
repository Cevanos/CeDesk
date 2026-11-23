"use client";

import { AlarmClock, CalendarClock, Clock } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { DatePicker } from "@/components/ui/date-picker";
import { MenuItem, Popover } from "@/components/ui/overlay";
import { Button } from "@/components/ui/primitives";
import { useSnoozeTicket, useUnsnoozeTicket } from "@/hooks/use-inbox-tools";
import {
  SNOOZE_MIN_MS,
  formatSnoozeTime,
  localInputToIso,
  localInputValue,
  snoozePresets,
} from "@/lib/snooze";

/**
 * Snooze time picker: three quick times and "Pick a date and time…". Calls `onPick` with an ISO
 * time that has a zone. Used on the ticket page and in the bulk bar.
 */
export function SnoozeMenu({
  onPick,
  pending,
  align = "right",
  trigger,
}: {
  onPick: (until: string) => void;
  pending?: boolean;
  align?: "left" | "right";
  trigger?: (p: {
    props: React.ButtonHTMLAttributes<HTMLButtonElement>;
    pending?: boolean;
  }) => React.ReactNode;
}) {
  return (
    <Popover
      align={align}
      className="w-64"
      trigger={({ props }) =>
        trigger ? (
          trigger({ props, pending })
        ) : (
          <Button variant="outline" {...props} loading={pending}>
            {!pending && <Clock className="size-4" aria-hidden />} Snooze
          </Button>
        )
      }
    >
      {(close) => <SnoozeChoices onPick={onPick} close={close} />}
    </Popover>
  );
}

function SnoozeChoices({ onPick, close }: { onPick: (until: string) => void; close: () => void }) {
  const [picking, setPicking] = useState(false);
  const [value, setValue] = useState("");
  // Read once per open: a stale "later today" from an hour ago would be wrong.
  const [presets] = useState(() => snoozePresets());
  const [min] = useState(() => localInputValue(new Date(Date.now() + SNOOZE_MIN_MS)));

  return (
    <div>
      {presets.map((p) => (
        <MenuItem
          key={p.id}
          onClick={() => {
            close();
            onPick(p.until);
          }}
          className="justify-between"
        >
          <span>{p.label}</span>
          <span className="text-xs text-muted-foreground">{formatSnoozeTime(p.until)}</span>
        </MenuItem>
      ))}
      {!picking ? (
        <MenuItem onClick={() => setPicking(true)}>
          <CalendarClock className="size-4" aria-hidden /> Pick a date and time…
        </MenuItem>
      ) : (
        <form
          className="space-y-2 p-2"
          onSubmit={(e) => {
            e.preventDefault();
            const iso = localInputToIso(value);
            if (!iso || new Date(iso).getTime() < Date.now() + SNOOZE_MIN_MS) {
              toast.error("Snooze until a time at least a minute from now.");
              return;
            }
            close();
            onPick(iso);
          }}
        >
          <DatePicker
            inline
            required
            type="datetime-local"
            aria-label="Snooze until"
            value={value}
            min={min}
            onChange={setValue}
          />
          <Button type="submit" size="sm" className="w-full" disabled={!value}>
            Snooze
          </Button>
        </form>
      )}
    </div>
  );
}

/** Header button for one ticket. Render only when the user can change it and it is not resolved. */
export function SnoozeButton({ ticketId, etag }: { ticketId: string; etag: string | null }) {
  const snooze = useSnoozeTicket(ticketId);
  return (
    <SnoozeMenu
      pending={snooze.isPending}
      onPick={(until) =>
        snooze.mutate(
          { until, etag },
          { onSuccess: () => toast.success(`Snoozed until ${formatSnoozeTime(until)}`) },
        )
      }
    />
  );
}

/** Top-of-ticket banner while snoozed. `canEdit` shows the Unsnooze button. */
export function SnoozeBanner({
  ticketId,
  until,
  canEdit,
}: {
  ticketId: string;
  until: string;
  canEdit: boolean;
}) {
  const unsnooze = useUnsnoozeTicket(ticketId);
  return (
    <div
      role="status"
      className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-info/30 bg-info-soft px-4 py-2.5 text-sm text-info"
    >
      <AlarmClock className="size-4 flex-none" aria-hidden />
      <span className="min-w-0 flex-1 font-medium">Snoozed until {formatSnoozeTime(until)}</span>
      {canEdit && (
        <Button
          variant="outline"
          size="sm"
          loading={unsnooze.isPending}
          onClick={() =>
            unsnooze.mutate(undefined, { onSuccess: () => toast.success("Unsnoozed") })
          }
        >
          Unsnooze
        </Button>
      )}
    </div>
  );
}

"use client";

import { Trash2 } from "lucide-react";
import { useState } from "react";

import {
  ExtraCard,
  Muted,
  QueryState,
  fmtMinutes,
  toastError,
  type ExtraProps,
} from "@/components/tickets/extras-shared";
import { useConfirm } from "@/components/ui/confirm";
import { Button, Input } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { useDeleteTime, useLogTime, useTimeLog } from "@/hooks/use-ticket-extras";
import { formatDateTime } from "@/lib/utils";

export function TimeCard({ ticketId, canEdit }: ExtraProps) {
  const { user } = useCurrentUser();
  const q = useTimeLog(ticketId);
  const log = useLogTime(ticketId);
  const del = useDeleteTime(ticketId);
  const { ask, dialog } = useConfirm();
  const [minutes, setMinutes] = useState("");
  const [note, setNote] = useState("");
  const [billable, setBillable] = useState(false);
  const isAdmin = user?.role === "admin";

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const m = Math.round(Number(minutes));
    if (!Number.isFinite(m) || m < 1) return;
    log.mutate(
      { minutes: m, note: note.trim() || undefined, billable },
      {
        onSuccess: () => {
          setMinutes("");
          setNote("");
        },
        onError: toastError,
      },
    );
  };

  return (
    <ExtraCard title="Time">
      {dialog}
      <QueryState q={q} />
      {q.data && (
        <>
          <div className="mb-2 flex gap-4 text-sm">
            <span>
              <span className="font-semibold">{fmtMinutes(q.data.total_minutes)}</span>{" "}
              <span className="text-muted-foreground">total</span>
            </span>
            <span>
              <span className="font-semibold">{fmtMinutes(q.data.billable_minutes)}</span>{" "}
              <span className="text-muted-foreground">billable</span>
            </span>
          </div>
          {q.data.entries.length ? (
            <ul className="space-y-1.5">
              {q.data.entries.map((e) => (
                <li key={e.id} className="flex items-start gap-2 text-sm">
                  <div className="min-w-0 flex-1">
                    <span className="font-medium">{fmtMinutes(Number(e.minutes) || 0)}</span>
                    {e.billable && <span className="ml-1 text-xs text-success">billable</span>}
                    {e.note && <span className="text-muted-foreground"> · {e.note}</span>}
                    <div className="text-xs text-muted-foreground">
                      {e.member_name ?? ""}
                      {e.created_at && ` ${formatDateTime(e.created_at)}`}
                    </div>
                  </div>
                  {canEdit && (isAdmin || !e.member_id || e.member_id === user?.id) && (
                    <button
                      aria-label="Remove time entry"
                      disabled={del.isPending}
                      className="grid size-7 flex-none place-items-center rounded-md text-muted-foreground hover:bg-surface-2 hover:text-danger"
                      onClick={() =>
                        ask({
                          title: "Remove this time entry?",
                          message: "The logged time is deleted from this ticket.",
                          detail: `${fmtMinutes(Number(e.minutes) || 0)}${e.note ? ` · ${e.note}` : ""}`,
                          confirmLabel: "Remove",
                          run: () => del.mutate(e.id, { onError: toastError }),
                        })
                      }
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <Muted>No time logged.</Muted>
          )}
          <form className="mt-3 space-y-2" onSubmit={submit}>
            <div className="flex gap-2">
              <Input
                type="number"
                min={1}
                max={1440}
                value={minutes}
                onChange={(e) => setMinutes(e.target.value)}
                placeholder="Minutes"
                aria-label="Minutes"
                disabled={!canEdit}
                className="h-9 w-24 flex-none"
              />
              <Input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Note (optional)"
                aria-label="Note"
                maxLength={500}
                disabled={!canEdit}
                className="h-9"
              />
            </div>
            <div className="flex items-center justify-between gap-2">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={billable}
                  disabled={!canEdit}
                  onChange={(e) => setBillable(e.target.checked)}
                />
                Billable
              </label>
              <Button
                type="submit"
                size="sm"
                disabled={!canEdit || !minutes}
                loading={log.isPending}
              >
                Log time
              </Button>
            </div>
          </form>
        </>
      )}
    </ExtraCard>
  );
}

"use client";

import { Trash2 } from "lucide-react";

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
import { bind, fieldError, useFormik, yup } from "@/lib/forms";
import { formatDateTime } from "@/lib/utils";

const timeSchema = yup.object({
  minutes: yup
    .number()
    .transform((v, o) => (o === "" ? undefined : v))
    .typeError("Enter minutes as a number")
    .required("Enter the minutes")
    .min(1, "At least 1 minute")
    .max(1440, "At most 1440 minutes (24 hours)"),
  note: yup.string().max(500),
  billable: yup.boolean(),
});

export function TimeCard({ ticketId, canEdit }: ExtraProps) {
  const { user } = useCurrentUser();
  const q = useTimeLog(ticketId);
  const log = useLogTime(ticketId);
  const del = useDeleteTime(ticketId);
  const { ask, dialog } = useConfirm();
  const isAdmin = user?.role === "admin";
  const form = useFormik({
    initialValues: { minutes: "", note: "", billable: false },
    validationSchema: timeSchema,
    onSubmit: (v, { resetForm }) => {
      const m = Math.round(Number(v.minutes));
      log.mutate(
        { minutes: m, note: v.note.trim() || undefined, billable: v.billable },
        {
          onSuccess: () => resetForm({ values: { minutes: "", note: "", billable: v.billable } }),
          onError: toastError,
        },
      );
    },
  });
  const { minutes } = form.values;

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
          {canEdit && (
            <form className="mt-3 space-y-2" onSubmit={form.handleSubmit} noValidate>
              <div className="flex gap-2">
                <Input
                  type="number"
                  min={1}
                  max={1440}
                  {...bind(form, "minutes")}
                  placeholder="Minutes"
                  aria-label="Minutes"
                  className="h-9 w-24 flex-none"
                />
                <Input
                  {...bind(form, "note")}
                  placeholder="Note (optional)"
                  aria-label="Note"
                  maxLength={500}
                  className="h-9"
                />
              </div>
              {(fieldError(form, "minutes") || fieldError(form, "note")) && (
                <p role="alert" className="text-xs text-danger">
                  {fieldError(form, "minutes") ?? fieldError(form, "note")}
                </p>
              )}
              <div className="flex items-center justify-between gap-2">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="size-4 accent-primary"
                    name="billable"
                    checked={form.values.billable}
                    onChange={form.handleChange}
                  />
                  Billable
                </label>
                <Button type="submit" size="sm" disabled={!minutes} loading={log.isPending}>
                  Log time
                </Button>
              </div>
            </form>
          )}
        </>
      )}
    </ExtraCard>
  );
}

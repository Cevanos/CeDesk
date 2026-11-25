"use client";

import { CalendarClock, LogOut, Pencil, Plus, Trash2 } from "lucide-react";
import { Fragment, useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { Dialog } from "@/components/ui/overlay";
import {
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
} from "@/components/ui/primitives";
import { SectionHeader } from "@/components/ui/section";
import { Switch } from "@/components/ui/switch";
import { DataTable, RowTh, TBody, Td, Th, THead, Tr } from "@/components/ui/table";
import { LoadError } from "@/components/workspace/list-shared";
import { useCurrentUser } from "@/hooks/use-auth";
import { useMemberOptions } from "@/hooks/use-workspace";
import {
  useCreateSchedule,
  useDeleteSchedule,
  useLeaveSchedule,
  useReportOptions,
  useSavedReports,
  useSchedules,
  useUpdateSchedule,
  type ScheduleInput,
} from "@/hooks/workspace/reports-explore";
import { ApiError } from "@/lib/api/client";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";
import {
  DASH,
  RANGE_PRESETS,
  WEEKDAYS,
  describeSchedule,
  type Cadence,
  type RangePreset,
  type ReportSchedule,
} from "@/lib/reports";
import { cn } from "@/lib/utils";

const MAX_RECIPIENTS = 10;
const msg = (e: unknown) => (e instanceof ApiError ? e.userMessage : "Something went wrong.");
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : DASH);

/** "Scheduled reports" tab. */
export function SchedulesTab() {
  const q = useSchedules();
  const update = useUpdateSchedule();
  const remove = useDeleteSchedule();
  const leave = useLeaveSchedule();
  const { ask, dialog } = useConfirm();
  const [editing, setEditing] = useState<ReportSchedule | "new" | null>(null);

  if (q.isPending) return <Skeleton className="h-48 rounded-xl" />;
  if (q.isError) return <LoadError error={q.error} />;
  const list = q.data;
  const timezone = list[0]?.timezone;

  return (
    <div className="space-y-4">
      <SectionHeader
        icon={CalendarClock}
        title="Scheduled reports"
        description="Email a report to your team on a schedule. Each email has the numbers and a link here."
        aside={
          <Button size="sm" onClick={() => setEditing("new")}>
            <Plus className="size-3.5" aria-hidden /> New schedule
          </Button>
        }
      />

      {list.length === 0 ? (
        <Card>
          <EmptyState title="No scheduled reports">
            Make one to get a report by email every day, week or month.
          </EmptyState>
        </Card>
      ) : (
        <Card>
          <DataTable minWidth="56rem">
            <THead>
              <tr>
                {["Name", "Report", "When", "Recipients", "Next run", "Last sent", "Owner"].map(
                  (h) => (
                    <Th key={h}>{h}</Th>
                  ),
                )}
                <Th>On</Th>
                <Th right>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </THead>
            <TBody className="divide-y-0">
              {list.map((s) => (
                <Fragment key={s.id}>
                  <Tr className={cn(!s.last_error && "border-b border-border")}>
                    <RowTh>{s.name}</RowTh>
                    <Td>{s.target.label}</Td>
                    <Td>{describeSchedule(s)}</Td>
                    <Td>{s.recipients.map((r) => r.name).join(", ") || DASH}</Td>
                    <Td className="whitespace-nowrap">{s.enabled ? when(s.next_run_at) : DASH}</Td>
                    <Td className="whitespace-nowrap">{when(s.last_sent_at)}</Td>
                    <Td>{s.owner_name ?? DASH}</Td>
                    <Td>
                      <Switch
                        checked={s.enabled}
                        disabled={!s.can_edit || update.isPending}
                        label={`${s.name} on or off`}
                        onChange={(v) =>
                          update
                            .mutateAsync({ id: s.id, enabled: v })
                            .catch((e) => toast.error(msg(e)))
                        }
                      />
                    </Td>
                    <Td>
                      <div className="flex justify-end gap-1">
                        {s.can_edit && (
                          <>
                            <Button
                              size="sm"
                              variant="ghost"
                              aria-label={`Edit ${s.name}`}
                              onClick={() => setEditing(s)}
                            >
                              <Pencil className="size-3.5" aria-hidden />
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              aria-label={`Delete ${s.name}`}
                              onClick={() =>
                                ask({
                                  title: "Delete this schedule?",
                                  message: "Delete this schedule? Recipients stop getting it.",
                                  detail: s.name,
                                  confirmLabel: "Delete",
                                  run: () =>
                                    remove.mutateAsync(s.id).catch((e) => toast.error(msg(e))),
                                })
                              }
                            >
                              <Trash2 className="size-3.5" aria-hidden />
                            </Button>
                          </>
                        )}
                        {s.is_recipient && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() =>
                              ask({
                                title: "Leave this schedule?",
                                message: "Stop getting this email?",
                                detail: s.name,
                                confirmLabel: "Leave",
                                tone: "warning",
                                run: () =>
                                  leave.mutateAsync(s.id).catch((e) => toast.error(msg(e))),
                              })
                            }
                          >
                            <LogOut className="size-3.5" aria-hidden /> Leave
                          </Button>
                        )}
                      </div>
                    </Td>
                  </Tr>
                  {s.last_error && (
                    <Tr className="border-b border-border">
                      <Td colSpan={9} className="pt-0 pb-3 text-xs text-danger">
                        <span role="alert">{s.last_error}</span>
                      </Td>
                    </Tr>
                  )}
                </Fragment>
              ))}
            </TBody>
          </DataTable>
        </Card>
      )}

      {editing && (
        <ScheduleDialog
          schedule={editing === "new" ? undefined : editing}
          timezone={editing === "new" ? timezone : editing.timezone}
          onClose={() => setEditing(null)}
        />
      )}
      {dialog}
    </div>
  );
}

/* ---------- new / edit ---------- */

function ScheduleDialog({
  schedule,
  timezone,
  onClose,
}: {
  schedule?: ReportSchedule;
  timezone?: string;
  onClose: () => void;
}) {
  const { user } = useCurrentUser();
  const options = useReportOptions();
  const saved = useSavedReports();
  const members = useMemberOptions();
  const create = useCreateSchedule();
  const update = useUpdateSchedule();

  const initialTarget = schedule
    ? schedule.target.report_id
      ? `r:${schedule.target.report_id}`
      : `p:${schedule.target.preset ?? ""}`
    : "";
  const form = useFormik({
    initialValues: { name: schedule?.name ?? "" },
    validationSchema: yup.object({
      name: yup
        .string()
        .trim()
        .min(1, "A schedule's name is 1-80 characters.")
        .max(80, "A schedule's name is 1-80 characters.")
        .required("A schedule's name is 1-80 characters."),
    }),
    onSubmit: (v) => save(v.name.trim()),
  });
  const [target, setTarget] = useState(initialTarget);
  const [cadence, setCadence] = useState<Cadence>(schedule?.cadence ?? "weekly");
  const [range, setRange] = useState<RangePreset>(schedule?.range ?? "last_7d");
  const [weekday, setWeekday] = useState(schedule?.weekday ?? 1);
  const [day, setDay] = useState(schedule?.day_of_month ?? 1);
  const [hour, setHour] = useState(schedule?.hour ?? 9);
  const [recipients, setRecipients] = useState<string[]>(
    schedule?.recipients.map((r) => r.member_id) ?? [],
  );
  const [error, setError] = useState<string | null>(null);
  const busy = create.isPending || update.isPending;

  const pickCadence = (c: Cadence) => {
    // The period each email covers follows the frequency until it is changed by hand.
    if (!schedule && range === (cadence === "monthly" ? "last_month" : "last_7d")) {
      setRange(c === "monthly" ? "last_month" : "last_7d");
    }
    setCadence(c);
  };

  const toggle = (id: string, on: boolean) =>
    setRecipients((r) => (on ? [...r, id] : r.filter((x) => x !== id)));
  const me = user?.id;

  const roleRank = (r: string) => (r === "admin" ? 0 : r === "supervisor" ? 1 : 2);
  const people = [...(members.data ?? [])]
    .filter((m) => m.is_active)
    .sort((a, b) => roleRank(a.role) - roleRank(b.role) || a.name.localeCompare(b.name));

  const save = async (trimmed: string) => {
    if (!target) return setError("Pick a report.");
    if (recipients.length < 1 || recipients.length > MAX_RECIPIENTS)
      return setError("A schedule goes to 1 to 10 members.");

    const body: ScheduleInput = {
      name: trimmed,
      range,
      cadence,
      hour,
      recipient_ids: recipients,
      ...(cadence === "weekly" ? { weekday } : {}),
      ...(cadence === "monthly" ? { day_of_month: day } : {}),
    };
    // A new target replaces the old one: only send it when it changed.
    if (!schedule || target !== initialTarget) {
      if (target.startsWith("r:")) body.report_id = target.slice(2);
      else body.preset = target.slice(2);
    }
    try {
      if (schedule) await update.mutateAsync({ id: schedule.id, ...body });
      else await create.mutateAsync(body);
      toast.success(schedule ? "Schedule saved" : "Schedule created");
      onClose();
    } catch (err) {
      setError(msg(err));
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={schedule ? "Edit schedule" : "New schedule"}
      className="sm:max-w-xl"
    >
      <form noValidate onSubmit={form.handleSubmit} className="space-y-4">
        <Field label="Name" htmlFor="sc-name" error={fieldError(form, "name")}>
          <Input id="sc-name" maxLength={80} {...bind(form, "name")} />
        </Field>
        <Field label="Report" htmlFor="sc-target">
          <Select
            id="sc-target"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            options={[
              { value: "", label: "Choose a report…" },
              ...(options.data?.presets ?? []).map((p) => ({
                value: `p:${p.key}`,
                label: `Built-in: ${p.label}`,
              })),
              ...(saved.data ?? []).map((r) => ({
                value: `r:${r.id}`,
                label: `Saved: ${r.name}`,
              })),
            ]}
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Period each email covers" htmlFor="sc-range">
            <Select
              id="sc-range"
              value={range}
              onChange={(e) => setRange(e.target.value as RangePreset)}
              options={RANGE_PRESETS}
            />
          </Field>
          <Field label="Frequency" htmlFor="sc-cadence">
            <Select
              id="sc-cadence"
              value={cadence}
              onChange={(e) => pickCadence(e.target.value as Cadence)}
              options={[
                { value: "daily", label: "Daily" },
                { value: "weekly", label: "Weekly" },
                { value: "monthly", label: "Monthly" },
              ]}
            />
          </Field>
          {cadence === "weekly" && (
            <Field label="Weekday" htmlFor="sc-weekday">
              <Select
                id="sc-weekday"
                value={String(weekday)}
                onChange={(e) => setWeekday(Number(e.target.value))}
                options={WEEKDAYS.map((d) => ({ value: String(d.value), label: d.label }))}
              />
            </Field>
          )}
          {cadence === "monthly" && (
            <Field label="Day of the month" htmlFor="sc-day" hint="1 to 28, so every month has it.">
              <Select
                id="sc-day"
                value={String(day)}
                onChange={(e) => setDay(Number(e.target.value))}
                options={Array.from({ length: 28 }, (_, i) => ({
                  value: String(i + 1),
                  label: String(i + 1),
                }))}
              />
            </Field>
          )}
          <Field label="Time" htmlFor="sc-hour">
            <Select
              id="sc-hour"
              value={String(hour)}
              onChange={(e) => setHour(Number(e.target.value))}
              options={Array.from({ length: 24 }, (_, i) => ({
                value: String(i),
                label: `${String(i).padStart(2, "0")}:00`,
              }))}
            />
          </Field>
          <Field
            label="Time zone"
            htmlFor="sc-tz"
            hint="The workspace's time zone. It can't be changed here."
          >
            <Input id="sc-tz" value={timezone ?? "Workspace time zone"} readOnly disabled />
          </Field>
        </div>

        <fieldset>
          <legend className="mb-1.5 text-[13px] font-medium text-foreground-2">
            Recipients ({recipients.length}/{MAX_RECIPIENTS})
          </legend>
          {me && (
            <label className="mb-2 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={recipients.includes(me)}
                onChange={(e) => toggle(me, e.target.checked)}
              />
              Send to me too
            </label>
          )}
          <ul className="max-h-44 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
            {people.map((m) => (
              <li key={m.id}>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={recipients.includes(m.id)}
                    disabled={!recipients.includes(m.id) && recipients.length >= MAX_RECIPIENTS}
                    onChange={(e) => toggle(m.id, e.target.checked)}
                  />
                  {m.name}
                  <span className="text-xs text-muted-foreground">{m.role}</span>
                </label>
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-muted-foreground">
            Recipients need the reports right. The server checks each one.
          </p>
        </fieldset>

        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy}>
            {schedule ? "Save" : "Create"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

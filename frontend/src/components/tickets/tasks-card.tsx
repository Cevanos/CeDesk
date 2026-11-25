"use client";

import {
  ExtraCard,
  Muted,
  QueryState,
  toastError,
  type ExtraProps,
} from "@/components/tickets/extras-shared";
import { DatePicker } from "@/components/ui/date-picker";
import { Button, Input, Select } from "@/components/ui/primitives";
import { useAddTask, useTasks, useUpdateTask } from "@/hooks/use-ticket-extras";
import { useAgents } from "@/hooks/use-workspace";
import { bind, bindSelect, fieldError, useFormik, yup } from "@/lib/forms";
import { formatDateTime } from "@/lib/utils";

const QUICK = [
  { v: "", label: "No due date" },
  { v: "1h", label: "In 1 hour" },
  { v: "4h", label: "In 4 hours" },
  { v: "tomorrow", label: "Tomorrow" },
  { v: "date", label: "Pick a date…" },
];

const taskSchema = yup.object({
  title: yup.string().trim().required("Enter a task").max(200, "Keep it under 200 characters"),
  due: yup.string(),
  date: yup.string().when("due", {
    is: "date",
    then: (s) => s.required("Pick a due date"),
  }),
  owner: yup.string(),
});

export function TasksCard({ ticketId, canEdit }: ExtraProps) {
  const q = useTasks(ticketId);
  const agents = useAgents();
  const add = useAddTask(ticketId);
  const update = useUpdateTask(ticketId);
  const form = useFormik({
    initialValues: { title: "", due: "", date: "", owner: "" },
    validationSchema: taskSchema,
    onSubmit: (v, { resetForm }) => {
      const dueVal =
        v.due === "date"
          ? v.date
            ? new Date(v.date).toISOString()
            : undefined
          : v.due || undefined;
      add.mutate(
        { title: v.title.trim(), due: dueVal, owner_id: v.owner || undefined },
        {
          onSuccess: () => resetForm({ values: { ...v, title: "", due: "", date: "" } }),
          onError: toastError,
        },
      );
    },
  });
  const { title, due } = form.values;

  return (
    <ExtraCard title="Tasks">
      <QueryState q={q} />
      {q.data &&
        (q.data.length ? (
          <ul className="space-y-1.5">
            {q.data.map((t) => (
              <li key={t.id} className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1 size-4 accent-primary"
                  checked={!!t.done}
                  disabled={!canEdit || update.isPending}
                  aria-label={`Mark "${t.title}" ${t.done ? "not done" : "done"}`}
                  onChange={(e) =>
                    update.mutate({ id: t.id, done: e.target.checked }, { onError: toastError })
                  }
                />
                <div className="min-w-0 flex-1">
                  <div className={t.done ? "text-muted-foreground line-through" : ""}>
                    {t.title}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {t.owner_name ?? "Unassigned"}
                    {t.due_at && ` · due ${formatDateTime(t.due_at)}`}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <Muted>No tasks.</Muted>
        ))}
      {q.data && (
        <form className="mt-3 space-y-2" onSubmit={form.handleSubmit} noValidate>
          <Input
            {...bind(form, "title")}
            placeholder="Add a task"
            aria-label="Task"
            maxLength={200}
            disabled={!canEdit}
            className="h-9"
          />
          {fieldError(form, "title") && (
            <p role="alert" className="text-xs text-danger">
              {fieldError(form, "title")}
            </p>
          )}
          {title && (
            <>
              <div className="grid grid-cols-2 gap-2">
                <Select {...bindSelect(form, "due")} aria-label="Due">
                  {QUICK.map((o) => (
                    <option key={o.v} value={o.v}>
                      {o.label}
                    </option>
                  ))}
                </Select>
                <Select {...bindSelect(form, "owner")} aria-label="Owner">
                  <option value="">Me</option>
                  {agents.data?.map((a) => (
                    <option key={a.member_id} value={a.member_id}>
                      {a.name}
                    </option>
                  ))}
                </Select>
              </div>
              {due === "date" && (
                <>
                  <DatePicker
                    type="datetime-local"
                    value={form.values.date}
                    onChange={(v) => void form.setFieldValue("date", v)}
                    aria-label="Due date"
                    className="h-9"
                  />
                  {fieldError(form, "date") && (
                    <p role="alert" className="text-xs text-danger">
                      {fieldError(form, "date")}
                    </p>
                  )}
                </>
              )}
              <Button type="submit" size="sm" loading={add.isPending} disabled={!canEdit}>
                Add task
              </Button>
            </>
          )}
        </form>
      )}
    </ExtraCard>
  );
}

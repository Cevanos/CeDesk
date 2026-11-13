"use client";

import { useState } from "react";

import {
  ExtraCard,
  Muted,
  QueryState,
  toastError,
  type ExtraProps,
} from "@/components/tickets/extras-shared";
import { Button, Input, Select } from "@/components/ui/primitives";
import { useAddTask, useTasks, useUpdateTask } from "@/hooks/use-ticket-extras";
import { useAgents } from "@/hooks/use-workspace";
import { formatDateTime } from "@/lib/utils";

const QUICK = [
  { v: "", label: "No due date" },
  { v: "1h", label: "In 1 hour" },
  { v: "4h", label: "In 4 hours" },
  { v: "tomorrow", label: "Tomorrow" },
  { v: "date", label: "Pick a date…" },
];

export function TasksCard({ ticketId, canEdit }: ExtraProps) {
  const q = useTasks(ticketId);
  const agents = useAgents();
  const add = useAddTask(ticketId);
  const update = useUpdateTask(ticketId);
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [date, setDate] = useState("");
  const [owner, setOwner] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    const dueVal =
      due === "date" ? (date ? new Date(date).toISOString() : undefined) : due || undefined;
    add.mutate(
      { title: title.trim(), due: dueVal, owner_id: owner || undefined },
      {
        onSuccess: () => {
          setTitle("");
          setDue("");
          setDate("");
        },
        onError: toastError,
      },
    );
  };

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
        <form className="mt-3 space-y-2" onSubmit={submit}>
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Add a task"
            aria-label="Task"
            maxLength={200}
            disabled={!canEdit}
            className="h-9"
          />
          {title && (
            <>
              <div className="grid grid-cols-2 gap-2">
                <Select value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due">
                  {QUICK.map((o) => (
                    <option key={o.v} value={o.v}>
                      {o.label}
                    </option>
                  ))}
                </Select>
                <Select value={owner} onChange={(e) => setOwner(e.target.value)} aria-label="Owner">
                  <option value="">Me</option>
                  {agents.data?.map((a) => (
                    <option key={a.member_id} value={a.member_id}>
                      {a.name}
                    </option>
                  ))}
                </Select>
              </div>
              {due === "date" && (
                <Input
                  type="datetime-local"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  aria-label="Due date"
                  className="h-9"
                />
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

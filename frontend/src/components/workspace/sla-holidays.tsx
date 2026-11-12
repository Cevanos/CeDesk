"use client";

import { CalendarDays, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Dialog } from "@/components/ui/overlay";
import { Badge, Button, EmptyState, Field, Input } from "@/components/ui/primitives";
import { FormError, fieldErrors } from "@/components/workspace/list-shared";
import { useDeleteHoliday, useSaveHoliday } from "@/hooks/use-workspace";
import type { Holiday } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/** Holidays sorted by date; each is flagged `past` once its day is before `today` (YYYY-MM-DD). */
export function sortHolidays(list: Holiday[], today: string) {
  return [...list]
    .sort((a, b) => a.day.localeCompare(b.day))
    .map((h) => ({ ...h, past: h.day < today }));
}

function dayLabel(day: string) {
  const d = new Date(`${day}T00:00:00`);
  return Number.isNaN(d.valueOf())
    ? day
    : d.toLocaleDateString([], {
        weekday: "short",
        day: "numeric",
        month: "short",
        year: "numeric",
      });
}

export function SlaHolidays({ holidays, canEdit }: { holidays: Holiday[]; canEdit: boolean }) {
  const del = useDeleteHoliday();
  const [today] = useState(() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  });
  const [editing, setEditing] = useState<Holiday | "new" | null>(null);
  const list = sortHolidays(holidays, today);

  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">Holidays</h3>
        {canEdit && (
          <Button size="sm" variant="outline" onClick={() => setEditing("new")}>
            <Plus className="size-3.5" /> Add holiday
          </Button>
        )}
      </div>
      {list.length === 0 ? (
        <EmptyState icon={<CalendarDays className="size-5" />} title="No holidays set">
          The SLA timer pauses on holidays. Add days your team is off.
        </EmptyState>
      ) : (
        <ul className="mt-2 divide-y divide-border rounded-lg border border-border">
          {list.map((h) => (
            <li
              key={h.id ?? `${h.day}-${h.name}`}
              className={cn(
                "flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2",
                h.past && "opacity-55",
              )}
            >
              <span className="w-44 text-sm font-medium tabular-nums">{dayLabel(h.day)}</span>
              <span className="min-w-0 flex-1 truncate text-sm">{h.name}</span>
              {h.past && <Badge>Past</Badge>}
              {canEdit && h.id && (
                <>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Edit ${h.name}`}
                    onClick={() => setEditing(h)}
                  >
                    <Pencil className="size-3.5" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Remove ${h.name}`}
                    disabled={del.isPending}
                    onClick={() => {
                      if (confirm(`Remove ${h.name} (${h.day})?`))
                        del.mutate(h.id!, { onSuccess: () => toast.success("Holiday removed") });
                    }}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "Add holiday" : "Edit holiday"}
      >
        {editing !== null && (
          <HolidayForm
            key={editing === "new" ? "new" : editing.id}
            holiday={editing === "new" ? null : editing}
            onClose={() => setEditing(null)}
          />
        )}
      </Dialog>
    </div>
  );
}

function HolidayForm({ holiday, onClose }: { holiday: Holiday | null; onClose: () => void }) {
  const save = useSaveHoliday();
  const [day, setDay] = useState(holiday?.day ?? "");
  const [name, setName] = useState(holiday?.name ?? "");
  const [errs, setErrs] = useState<Record<string, string>>({});

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const next: Record<string, string> = {};
        if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) next.day = "Pick a date";
        if (!name.trim()) next.name = "Enter a name, e.g. Diwali";
        setErrs(next);
        if (Object.keys(next).length) return;
        save.mutate(
          { id: holiday?.id, day, name: name.trim() },
          {
            onSuccess: () => {
              toast.success("Holiday saved");
              onClose();
            },
            onError: (err) => setErrs(fieldErrors(err)),
          },
        );
      }}
    >
      <Field label="Date" htmlFor="h-day" error={errs.day}>
        <Input id="h-day" type="date" value={day} onChange={(e) => setDay(e.target.value)} />
      </Field>
      <Field label="Name" htmlFor="h-name" error={errs.name}>
        <Input id="h-name" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <FormError message={errs._form} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          Save
        </Button>
      </div>
    </form>
  );
}

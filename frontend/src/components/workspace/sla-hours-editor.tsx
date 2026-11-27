"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Card, Button, Field, Input, Select } from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { FormError, fieldErrors } from "@/components/workspace/list-shared";
import { useSaveSlaCalendar } from "@/hooks/use-workspace";
import type { SlaSettings } from "@/lib/api/types";
import { bind, bindSelect, fieldError, useFormik } from "@/lib/forms";
import { cn } from "@/lib/utils";

/** Index 0 = Monday ... 6 = Sunday. The API sends ISO weekdays: 1 = Monday ... 7 = Sunday. */
export const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const FALLBACK_ZONES = [
  "UTC",
  "Asia/Kolkata",
  "Asia/Dubai",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
  "Europe/London",
  "Europe/Berlin",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
];

export function timeZones(current?: string): string[] {
  let zones: string[] = FALLBACK_ZONES;
  try {
    const all = (
      Intl as unknown as { supportedValuesOf?: (k: string) => string[] }
    ).supportedValuesOf?.("timeZone");
    if (all?.length) zones = all.includes("UTC") ? all : ["UTC", ...all];
  } catch {
    /* older browsers keep the short list */
  }
  return current && !zones.includes(current) ? [current, ...zones] : zones;
}

/** "HH:MM" strings compare correctly as text. */
export function hoursError(opens: string, closes: string): string | undefined {
  if (!opens || !closes) return "Set both opening and closing time.";
  if (closes <= opens) return "Closing time must be after opening time.";
  return undefined;
}

type Calendar = SlaSettings["calendar"];

export function SlaHoursEditor({ calendar, canEdit }: { calendar: Calendar; canEdit: boolean }) {
  const save = useSaveSlaCalendar();
  const [srv, setSrv] = useState<Record<string, string>>({});
  const form = useFormik({
    initialValues: {
      timezone: calendar.timezone,
      days: calendar.days as number[],
      opens: calendar.opens?.slice(0, 5) ?? "09:00",
      closes: calendar.closes?.slice(0, 5) ?? "18:00",
      always_open: calendar.always_open,
    },
    validate: (v) => {
      const next: Record<string, string> = {};
      if (!v.always_open) {
        if (v.days.length === 0) next.days = "Pick at least one open day.";
        const t = hoursError(v.opens, v.closes);
        if (t) next.hours = t;
      }
      if (!v.timezone) next.timezone = "Choose a time zone.";
      return next;
    },
    onSubmit: (v) => {
      setSrv({});
      save.mutate(
        {
          timezone: v.timezone,
          days: [...v.days].sort((x, y) => x - y),
          opens: v.opens,
          closes: v.closes,
          always_open: v.always_open,
        },
        {
          onSuccess: () => toast.success("Business hours saved"),
          onError: (e) => setSrv(fieldErrors(e)),
        },
      );
    },
  });
  const { days, always_open: always } = form.values;
  const errs: Record<string, string | undefined> = {
    ...srv,
    days: fieldError(form, "days") ?? srv.days,
    hours: fieldError(form, "hours") ?? srv.hours,
    timezone: fieldError(form, "timezone") ?? srv.timezone,
  };
  // `hours` is not a field of its own: show it once the form was submitted.
  const raw = form.errors as Record<string, unknown>;
  if (form.submitCount > 0 && typeof raw.hours === "string") errs.hours = raw.hours;
  if (form.submitCount > 0 && typeof raw.days === "string") errs.days = raw.days;

  if (!canEdit) {
    return (
      <p>
        {calendar.always_open
          ? "Open 24 hours, every day."
          : `${calendar.days
              .slice()
              .sort((a, b) => a - b)
              .map((d) => DAYS[d - 1] ?? String(d))
              .join(", ")} · ${calendar.opens}–${calendar.closes} (${calendar.timezone})`}
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 rounded-lg bg-surface-2/70 px-3.5 py-3">
        <div>
          <p id="always-open" className="text-sm font-medium">
            Open 24/7
          </p>
          <p id="always-open-d" className="text-xs text-muted-foreground">
            The SLA timer never pauses.
          </p>
        </div>
        <Switch
          checked={always}
          onChange={(v) => form.setFieldValue("always_open", v)}
          labelledBy="always-open"
          describedBy="always-open-d"
        />
      </div>

      <div
        className={cn("space-y-4", always && "pointer-events-none opacity-45")}
        aria-hidden={always}
      >
        <Field label="Time zone" htmlFor="cal-tz" error={errs.timezone}>
          <Select
            id="cal-tz"
            {...bindSelect(form, "timezone")}
            disabled={always}
            options={timeZones(calendar.timezone).map((z) => ({ value: z, label: z }))}
          />
        </Field>

        <fieldset disabled={always}>
          <legend className="text-[13px] font-medium text-foreground-2">Open days</legend>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {DAYS.map((label, i) => {
              const d = i + 1;
              const on = days.includes(d);
              return (
                <button
                  key={label}
                  type="button"
                  aria-pressed={on}
                  onClick={() =>
                    form.setFieldValue("days", on ? days.filter((x) => x !== d) : [...days, d])
                  }
                  className={cn(
                    "h-9 min-w-12 rounded-lg border px-3 text-sm font-semibold transition-colors",
                    on
                      ? "border-primary bg-primary-soft text-primary-ink"
                      : "border-border bg-surface text-muted-foreground hover:border-border-strong",
                  )}
                >
                  {label}
                </button>
              );
            })}
          </div>
          {errs.days && (
            <p role="alert" className="mt-1 text-xs text-danger">
              {errs.days}
            </p>
          )}
        </fieldset>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Opens" htmlFor="cal-open">
            <Input id="cal-open" type="time" {...bind(form, "opens")} disabled={always} />
          </Field>
          <Field label="Closes" htmlFor="cal-close">
            <Input
              id="cal-close"
              type="time"
              {...bind(form, "closes")}
              disabled={always}
              aria-invalid={!!errs.hours}
            />
          </Field>
        </div>
        {errs.hours && (
          <p role="alert" className="text-xs text-danger">
            {errs.hours}
          </p>
        )}
      </div>

      <FormError message={srv._form} />
      <div className="flex justify-end">
        <Button onClick={() => form.submitForm()} loading={save.isPending}>
          Save business hours
        </Button>
      </div>
    </div>
  );
}

/** Wrapper so the section keeps the same card look as the others. */
export function HoursCard({ children }: { children: React.ReactNode }) {
  return <Card className="mt-3 space-y-4 p-4 text-sm">{children}</Card>;
}

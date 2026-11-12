"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Card, Button, Field, Input, Select } from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { FormError, fieldErrors } from "@/components/workspace/list-shared";
import { useSaveSlaCalendar } from "@/hooks/use-workspace";
import type { SlaSettings } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/** 0 = Monday ... 6 = Sunday, as the API sends them. */
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
  const [tz, setTz] = useState(calendar.timezone);
  const [days, setDays] = useState<number[]>(calendar.days);
  const [opens, setOpens] = useState(calendar.opens?.slice(0, 5) ?? "09:00");
  const [closes, setCloses] = useState(calendar.closes?.slice(0, 5) ?? "18:00");
  const [always, setAlways] = useState(calendar.always_open);
  const [errs, setErrs] = useState<Record<string, string>>({});

  if (!canEdit) {
    return (
      <p>
        {calendar.always_open
          ? "Open 24 hours, every day."
          : `${calendar.days
              .slice()
              .sort((a, b) => a - b)
              .map((d) => DAYS[d] ?? String(d))
              .join(", ")} · ${calendar.opens}–${calendar.closes} (${calendar.timezone})`}
      </p>
    );
  }

  const submit = () => {
    const next: Record<string, string> = {};
    if (!always) {
      if (days.length === 0) next.days = "Pick at least one open day.";
      const t = hoursError(opens, closes);
      if (t) next.hours = t;
    }
    if (!tz) next.timezone = "Choose a time zone.";
    setErrs(next);
    if (Object.keys(next).length) return;
    save.mutate(
      {
        timezone: tz,
        days: [...days].sort((a, b) => a - b),
        opens,
        closes,
        always_open: always,
      },
      {
        onSuccess: () => toast.success("Business hours saved"),
        onError: (e) => setErrs(fieldErrors(e)),
      },
    );
  };

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
          onChange={setAlways}
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
            value={tz}
            disabled={always}
            options={timeZones(calendar.timezone).map((z) => ({ value: z, label: z }))}
            onChange={(e) => setTz(e.target.value)}
          />
        </Field>

        <fieldset disabled={always}>
          <legend className="text-[13px] font-medium text-foreground-2">Open days</legend>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {DAYS.map((label, d) => {
              const on = days.includes(d);
              return (
                <button
                  key={label}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setDays(on ? days.filter((x) => x !== d) : [...days, d])}
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
            <Input
              id="cal-open"
              type="time"
              value={opens}
              disabled={always}
              onChange={(e) => setOpens(e.target.value)}
            />
          </Field>
          <Field label="Closes" htmlFor="cal-close">
            <Input
              id="cal-close"
              type="time"
              value={closes}
              disabled={always}
              aria-invalid={!!errs.hours}
              onChange={(e) => setCloses(e.target.value)}
            />
          </Field>
        </div>
        {errs.hours && (
          <p role="alert" className="text-xs text-danger">
            {errs.hours}
          </p>
        )}
      </div>

      <FormError message={errs._form} />
      <div className="flex justify-end">
        <Button onClick={submit} loading={save.isPending}>
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

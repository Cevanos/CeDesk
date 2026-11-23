"use client";

import { format } from "date-fns";
import { CalendarDays, ChevronLeft, ChevronRight, Clock, X } from "lucide-react";
import * as React from "react";
import { DayPicker } from "react-day-picker";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";

/** Values are the same strings a native input uses: "YYYY-MM-DD" or "YYYY-MM-DDTHH:mm" (local time). */
const pad = (n: number) => String(n).padStart(2, "0");
const toDate = (v: string): Date | undefined => {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(v);
  if (!m) return undefined;
  const d = new Date(+m[1]!, +m[2]! - 1, +m[3]!, +(m[4] ?? 0), +(m[5] ?? 0));
  return Number.isNaN(d.getTime()) ? undefined : d;
};
const dayStr = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const timeStr = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

const POPUP_W = 304;

const selectCls =
  "h-9 rounded-lg border border-input bg-surface px-2 text-sm tabular-nums transition-colors hover:border-border-strong focus:border-primary focus:ring-4 focus:ring-primary/15 focus:outline-none";

export interface DatePickerProps {
  value: string;
  onChange: (value: string) => void;
  /** "date" picks a day; "datetime-local" also picks a time. */
  type?: "date" | "datetime-local";
  min?: string;
  max?: string;
  id?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
  "aria-invalid"?: boolean;
  /** Skip the Clear button where a value is always needed. */
  required?: boolean;
  /** Render the calendar in place (no trigger or popup), for use inside a menu. */
  inline?: boolean;
  /** Start with the calendar open (an edit-in-place cell). */
  defaultOpen?: boolean;
  /** Called when the calendar closes. */
  onClose?: () => void;
}

export function DatePicker({
  value,
  onChange,
  type = "date",
  min,
  max,
  id,
  placeholder,
  disabled,
  className,
  required,
  inline,
  defaultOpen,
  onClose,
  ...aria
}: DatePickerProps) {
  const withTime = type === "datetime-local";
  const [open, setOpen] = React.useState(!!defaultOpen);
  const [pos, setPos] = React.useState<{ top: number; left: number } | null>(null);
  const triggerRef = React.useRef<HTMLButtonElement>(null);
  const popRef = React.useRef<HTMLDivElement>(null);

  const selected = toDate(value);
  const minD = min ? toDate(min) : undefined;
  const maxD = max ? toDate(max) : undefined;
  const [month, setMonth] = React.useState<Date>(selected ?? new Date());

  const onCloseRef = React.useRef(onClose);
  React.useEffect(() => {
    onCloseRef.current = onClose;
  });
  const close = React.useCallback(
    (refocus = true) => {
      if (inline) return;
      setOpen(false);
      onCloseRef.current?.();
      if (refocus) triggerRef.current?.focus();
    },
    [inline],
  );

  const place = React.useCallback(() => {
    const t = triggerRef.current;
    const p = popRef.current;
    if (!t) return;
    const r = t.getBoundingClientRect();
    const h = p?.offsetHeight ?? 360;
    const below = window.innerHeight - r.bottom;
    const top = below >= h + 12 || r.top < h + 12 ? r.bottom + 6 : r.top - h - 6;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - POPUP_W - 8));
    setPos({ top: Math.max(8, top), left });
  }, []);

  React.useLayoutEffect(() => {
    if (!open || inline) return;
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, inline, place]);

  React.useEffect(() => {
    if (!open || inline) return;
    const down = (e: MouseEvent) => {
      const n = e.target as Node;
      if (popRef.current?.contains(n) || triggerRef.current?.contains(n)) return;
      close(false);
    };
    // Captured on window so Escape closes only the picker, not a dialog around it.
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      close();
    };
    document.addEventListener("mousedown", down);
    window.addEventListener("keydown", key, true);
    return () => {
      document.removeEventListener("mousedown", down);
      window.removeEventListener("keydown", key, true);
    };
  }, [open, inline, close]);

  const clamp = (d: Date) => {
    if (minD && d < minD) return minD;
    if (maxD && d > maxD) return maxD;
    return d;
  };
  const emit = (d: Date) => {
    const c = withTime ? clamp(d) : d;
    onChange(withTime ? `${dayStr(c)}T${timeStr(c)}` : dayStr(c));
  };

  const pickDay = (d: Date | undefined) => {
    if (!d) return;
    const base = new Date(d);
    if (withTime) {
      const t = selected ?? minD ?? new Date();
      base.setHours(t.getHours(), t.getMinutes());
    }
    emit(base);
    if (!withTime) close();
  };

  const setTime = (h: number, m: number) => {
    const d = new Date(selected ?? minD ?? new Date());
    d.setHours(h, m, 0, 0);
    emit(d);
  };

  const label = selected
    ? withTime
      ? format(selected, "EEE, d MMM yyyy · h:mm a")
      : format(selected, "EEE, d MMM yyyy")
    : null;

  const hours = selected?.getHours() ?? 9;
  const minutes = selected?.getMinutes() ?? 0;
  const minuteOptions = Array.from(new Set([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, minutes]))
    .filter((n) => n < 60)
    .sort((a, b) => a - b);

  const panel = (
    <div
      ref={popRef}
      role="dialog"
      aria-label={aria["aria-label"] ?? (withTime ? "Choose date and time" : "Choose date")}
      style={
        inline
          ? undefined
          : {
              top: pos?.top ?? 0,
              left: pos?.left ?? 0,
              width: POPUP_W,
              visibility: pos ? "visible" : "hidden",
            }
      }
      className={
        inline
          ? "w-full"
          : "fixed z-[80] animate-rise rounded-2xl border border-border bg-surface p-3 shadow-hero"
      }
    >
      <DayPicker
        mode="single"
        selected={selected ? startOfDay(selected) : undefined}
        onSelect={pickDay}
        month={month}
        onMonthChange={setMonth}
        showOutsideDays
        fixedWeeks
        weekStartsOn={1}
        disabled={[
          ...(minD ? [{ before: startOfDay(minD) }] : []),
          ...(maxD ? [{ after: startOfDay(maxD) }] : []),
        ]}
        classNames={{
          root: "w-full",
          months: "relative",
          month: "space-y-2",
          month_caption: "flex h-9 items-center justify-center",
          caption_label: "text-sm font-semibold tracking-tight",
          nav: "absolute inset-x-0 top-0 flex h-9 items-center justify-between",
          button_previous:
            "grid size-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:ring-4 focus-visible:ring-primary/15 focus-visible:outline-none",
          button_next:
            "grid size-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:ring-4 focus-visible:ring-primary/15 focus-visible:outline-none",
          month_grid: "w-full border-collapse",
          weekdays: "",
          weekday:
            "h-8 text-center text-[11px] font-semibold tracking-wider text-subtle-foreground uppercase",
          week: "",
          day: "p-0 text-center",
          day_button:
            "mx-auto grid size-9 place-items-center rounded-lg text-sm tabular-nums transition-colors hover:bg-primary-soft hover:text-primary-ink focus-visible:ring-4 focus-visible:ring-primary/15 focus-visible:outline-none",
          today:
            "[&>button]:font-bold [&>button]:text-primary-ink [&>button]:ring-1 [&>button]:ring-primary/40",
          selected:
            "[&>button]:bg-primary [&>button]:text-primary-foreground [&>button]:shadow-sm [&>button]:hover:bg-primary-hover [&>button]:hover:text-primary-foreground",
          outside: "[&>button]:text-subtle-foreground/60",
          disabled: "[&>button]:pointer-events-none [&>button]:opacity-30",
          hidden: "invisible",
        }}
        components={{
          Chevron: ({ orientation }) =>
            orientation === "left" ? (
              <ChevronLeft className="size-4" />
            ) : (
              <ChevronRight className="size-4" />
            ),
        }}
      />

      {withTime && (
        <div className="mt-2 flex items-center gap-2 border-t border-border pt-3">
          <Clock className="size-4 text-subtle-foreground" aria-hidden />
          <select
            aria-label="Hour"
            className={selectCls}
            value={hours}
            onChange={(e) => setTime(+e.target.value, minutes)}
          >
            {Array.from({ length: 24 }, (_, h) => (
              <option key={h} value={h}>
                {pad(h)}
              </option>
            ))}
          </select>
          <span aria-hidden className="text-muted-foreground">
            :
          </span>
          <select
            aria-label="Minute"
            className={selectCls}
            value={minutes}
            onChange={(e) => setTime(hours, +e.target.value)}
          >
            {minuteOptions.map((m) => (
              <option key={m} value={m}>
                {pad(m)}
              </option>
            ))}
          </select>
          <span className="ml-auto text-xs text-muted-foreground">
            {format(new Date(2000, 0, 1, hours, minutes), "h:mm a")}
          </span>
        </div>
      )}

      <div className="mt-2 flex items-center justify-between border-t border-border pt-2">
        <button
          type="button"
          onClick={() => {
            const now = new Date();
            const next = withTime ? now : startOfDay(now);
            setMonth(next);
            if (!withTime) {
              const ok = (!minD || next >= startOfDay(minD)) && (!maxD || next <= maxD);
              if (ok) {
                emit(next);
                close();
              }
            } else emit(next);
          }}
          className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-primary-ink transition-colors hover:bg-primary-soft"
        >
          {withTime ? "Now" : "Today"}
        </button>
        <div className="flex items-center gap-1">
          {!required && value && (
            <button
              type="button"
              onClick={() => {
                onChange("");
                close();
              }}
              className="inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"
            >
              <X className="size-3" /> Clear
            </button>
          )}
          {withTime && (
            <button
              type="button"
              onClick={() => close()}
              className="rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary-hover"
            >
              Done
            </button>
          )}
        </div>
      </div>
    </div>
  );

  if (inline) return panel;

  return (
    <>
      <button
        ref={triggerRef}
        id={id}
        type="button"
        disabled={disabled}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={aria["aria-label"]}
        data-invalid={aria["aria-invalid"] || undefined}
        onClick={() => {
          if (open) close(false);
          else {
            setMonth(selected ?? minD ?? new Date());
            setOpen(true);
          }
        }}
        className={cn(
          "flex h-10 w-full items-center gap-2 rounded-lg border border-input bg-surface px-3 text-left text-sm text-foreground transition-colors hover:border-border-strong focus:border-primary focus:ring-4 focus:ring-primary/15 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50 data-[invalid=true]:border-danger",
          open && "border-primary ring-4 ring-primary/15",
          className,
        )}
      >
        <CalendarDays className="size-4 shrink-0 text-subtle-foreground" aria-hidden />
        <span className={cn("min-w-0 flex-1 truncate", !label && "text-subtle-foreground")}>
          {label ?? placeholder ?? (withTime ? "Pick date & time" : "Pick a date")}
        </span>
      </button>

      {open && typeof document !== "undefined" && createPortal(panel, document.body)}
    </>
  );
}

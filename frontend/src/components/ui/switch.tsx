"use client";

import { cn } from "@/lib/utils";

/** On/off switch. Name it with `labelledBy` (an element id) or `label`. */
export function Switch({
  checked,
  onChange,
  label,
  labelledBy,
  describedBy,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
  labelledBy?: string;
  describedBy?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-6 w-11 shrink-0 rounded-full transition-colors focus-visible:ring-4 focus-visible:ring-primary/25 focus-visible:outline-none disabled:opacity-50",
        checked ? "bg-primary" : "bg-surface-3 ring-1 ring-border-strong ring-inset",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute top-0.5 left-0.5 size-5 rounded-full bg-white shadow transition-transform",
          checked && "translate-x-5",
        )}
      />
    </button>
  );
}

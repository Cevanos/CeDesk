"use client";

import { Angry, Frown, Laugh, Meh, Smile, type LucideIcon } from "lucide-react";
import { useId, useRef } from "react";

import { cn } from "@/lib/utils";

export const SCORES: { score: number; label: string; icon: LucideIcon; tone: string }[] = [
  { score: 1, label: "Very unhappy", icon: Angry, tone: "text-danger" },
  { score: 2, label: "Unhappy", icon: Frown, tone: "text-warning" },
  { score: 3, label: "Okay", icon: Meh, tone: "text-info" },
  { score: 4, label: "Happy", icon: Smile, tone: "text-success" },
  { score: 5, label: "Delighted", icon: Laugh, tone: "text-success" },
];

export function scoreLabel(score: number) {
  return SCORES.find((s) => s.score === score)?.label ?? "";
}

/**
 * 1-5 rating as a radio group (arrow keys move, Space/Enter picks), shown as faces.
 * `readOnly` renders the same row without interaction, for an existing rating.
 */
export function RatingPicker({
  value,
  onChange,
  legend = "How would you rate your experience?",
  readOnly,
  disabled,
}: {
  value: number | null;
  onChange?: (score: number) => void;
  legend?: string;
  readOnly?: boolean;
  disabled?: boolean;
}) {
  const id = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const inactive = readOnly || disabled;

  const move = (from: number, dir: 1 | -1) => {
    const next = (from + dir + SCORES.length) % SCORES.length;
    onChange?.(SCORES[next]!.score);
    refs.current[next]?.focus();
  };

  return (
    <fieldset className="min-w-0" disabled={disabled}>
      <legend className="mb-3 text-sm font-medium text-foreground-2">{legend}</legend>
      <div role="radiogroup" aria-labelledby={`${id}-l`} className="grid grid-cols-5 gap-2 sm:gap-3">
        <span id={`${id}-l`} className="sr-only">
          {legend}
        </span>
        {SCORES.map(({ score, label, icon: Icon, tone }, i) => {
          const on = value === score;
          return (
            <button
              key={score}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="radio"
              aria-checked={on}
              aria-label={`${score} of 5, ${label}`}
              tabIndex={on || (value === null && i === 0) ? 0 : -1}
              disabled={inactive}
              onClick={() => onChange?.(score)}
              onKeyDown={(e) => {
                if (e.key === "ArrowRight" || e.key === "ArrowDown") {
                  e.preventDefault();
                  move(i, 1);
                } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
                  e.preventDefault();
                  move(i, -1);
                }
              }}
              className={cn(
                "group flex min-h-20 flex-col items-center justify-center gap-1.5 rounded-2xl border px-1 py-3 transition-all duration-150 focus-visible:ring-4 focus-visible:ring-primary/25 focus-visible:outline-none",
                on
                  ? "border-primary bg-primary-soft shadow-pop"
                  : "border-border bg-surface hover:-translate-y-0.5 hover:border-border-strong hover:shadow-pop",
                inactive && !on && "opacity-50",
                inactive && "pointer-events-none",
              )}
            >
              <Icon
                className={cn(
                  "size-8 transition-transform duration-150 sm:size-9",
                  on ? [tone, "scale-110"] : "text-subtle-foreground group-hover:scale-105",
                )}
                strokeWidth={on ? 2.2 : 1.8}
                aria-hidden
              />
              <span
                className={cn(
                  "hidden text-[11px] leading-tight font-medium sm:block",
                  on ? "text-primary-ink" : "text-muted-foreground",
                )}
              >
                {label}
              </span>
              <span className="text-xs font-semibold sm:hidden">{score}</span>
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

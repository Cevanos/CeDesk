"use client";

import { ArrowDown, ArrowUp, TriangleAlert } from "lucide-react";

import { ApiError } from "@/lib/api/client";

/** New id order after moving item `i` one step up (-1) or down (+1); null if it can't move. */
export function movedIds(ids: string[], i: number, dir: -1 | 1): string[] | null {
  const j = i + dir;
  const a = ids[i];
  const b = ids[j];
  if (a === undefined || b === undefined) return null;
  const next = [...ids];
  next[i] = b;
  next[j] = a;
  return next;
}

export function ReorderButtons({
  name,
  index,
  count,
  disabled,
  onMove,
}: {
  name: string;
  index: number;
  count: number;
  disabled?: boolean;
  onMove: (dir: -1 | 1) => void;
}) {
  return (
    <div className="flex flex-col">
      <button
        type="button"
        aria-label={`Move ${name} up`}
        disabled={index === 0 || disabled}
        onClick={() => onMove(-1)}
        className="text-muted-foreground hover:text-foreground disabled:opacity-30"
      >
        <ArrowUp className="size-4" />
      </button>
      <button
        type="button"
        aria-label={`Move ${name} down`}
        disabled={index === count - 1 || disabled}
        onClick={() => onMove(1)}
        className="text-muted-foreground hover:text-foreground disabled:opacity-30"
      >
        <ArrowDown className="size-4" />
      </button>
    </div>
  );
}

/** Amber warning for a rule or macro the server says can't run (`problems`). */
export function Problems({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div
      role="note"
      className="mt-2 flex gap-2 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning"
    >
      <TriangleAlert className="mt-0.5 size-3.5 flex-none" aria-hidden />
      <ul className="space-y-0.5">
        {items.map((p) => (
          <li key={p}>{p}</li>
        ))}
      </ul>
    </div>
  );
}

/** Field errors from a 422 as { field: message }; anything else is a toast elsewhere. */
export function fieldErrors(e: unknown): Record<string, string> {
  if (!(e instanceof ApiError) || e.status !== 422) return {};
  const byField = Object.fromEntries(e.details.map((d) => [d.field, d.message]));
  // Fields a form does not render (e.g. `conditions.0.words`) still reach the user here.
  return { ...byField, _form: e.details.map((d) => d.message).join(" ") || e.message };
}

export function FormError({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <p
      role="alert"
      className="rounded-lg border border-danger/25 bg-danger-soft px-3 py-2 text-sm text-danger"
    >
      {message}
    </p>
  );
}

export function LoadError({ error }: { error: unknown }) {
  return (
    <p
      role="alert"
      className="rounded-lg border border-danger/25 bg-danger-soft p-4 text-sm text-danger"
    >
      {error instanceof ApiError ? error.userMessage : "We couldn't load this."}
    </p>
  );
}

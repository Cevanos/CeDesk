"use client";

import { X } from "lucide-react";
import { useId, useState } from "react";

import { cn } from "@/lib/utils";

/** Trim, lowercase and collapse spaces; empty strings become "". */
export function normalizeTag(raw: string): string {
  return raw.trim().toLowerCase().replace(/\s+/g, " ");
}

/**
 * Add the typed text (which may hold several comma-separated tags) to `tags`.
 * Empty and duplicate tags are dropped; `validate` returns an error message to reject one.
 */
export function addTags(
  tags: string[],
  input: string,
  validate?: (tag: string) => string | null,
  transform?: (tag: string) => string,
): { tags: string[]; error: string | null } {
  let next = tags;
  let error: string | null = null;
  for (const part of input.split(",")) {
    const tag = normalizeTag(transform ? transform(normalizeTag(part)) : part);
    if (!tag || next.includes(tag)) continue;
    const bad = validate?.(tag);
    if (bad) {
      error = bad;
      continue;
    }
    next = [...next, tag];
  }
  return { tags: next, error };
}

/**
 * Chips with a text box. Enter or a comma adds, Backspace on an empty box removes the last
 * chip, and each chip has its own remove button.
 */
export function TagInput({
  label,
  tags,
  onChange,
  placeholder,
  validate,
  transform,
  disabled,
  className,
}: {
  label: string;
  tags: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
  validate?: (tag: string) => string | null;
  /** Clean a tag before it is checked and added (e.g. strip a leading @). */
  transform?: (tag: string) => string;
  disabled?: boolean;
  className?: string;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const errId = useId();

  const commit = () => {
    if (!text.trim()) return;
    const r = addTags(tags, text, validate, transform);
    setError(r.error);
    if (r.tags !== tags) onChange(r.tags);
    setText(r.error ? text : "");
  };

  return (
    <div className={className}>
      <div
        className={cn(
          "flex min-h-10 flex-wrap items-center gap-1.5 rounded-lg border border-input bg-surface px-2 py-1.5 transition-colors focus-within:border-primary focus-within:ring-4 focus-within:ring-primary/15 hover:border-border-strong",
          disabled && "cursor-not-allowed opacity-50",
          error && "border-danger",
        )}
      >
        {tags.map((t) => (
          <span
            key={t}
            className="inline-flex items-center gap-1 rounded-full bg-primary-soft py-0.5 pr-1 pl-2.5 text-[12.5px] font-medium text-primary-ink"
          >
            {t}
            <button
              type="button"
              disabled={disabled}
              aria-label={`Remove ${t}`}
              onClick={() => onChange(tags.filter((x) => x !== t))}
              className="grid size-4 place-items-center rounded-full hover:bg-primary/15"
            >
              <X className="size-3" />
            </button>
          </span>
        ))}
        <input
          aria-label={label}
          aria-invalid={!!error}
          aria-describedby={error ? errId : undefined}
          disabled={disabled}
          value={text}
          placeholder={tags.length ? "" : placeholder}
          onChange={(e) => {
            const v = e.target.value;
            setError(null);
            if (v.includes(",")) {
              const r = addTags(tags, v, validate, transform);
              setError(r.error);
              if (r.tags !== tags) onChange(r.tags);
              setText(r.error ? v.replace(/,/g, "") : "");
            } else setText(v);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit();
            } else if (e.key === "Backspace" && !text && tags.length) {
              onChange(tags.slice(0, -1));
            }
          }}
          onBlur={commit}
          className="min-w-24 flex-1 bg-transparent px-1 py-0.5 text-sm outline-none placeholder:text-subtle-foreground"
        />
      </div>
      {error && (
        <p id={errId} role="alert" className="mt-1 text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

"use client";

import { Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";

import { PRIORITIES, PRIORITY_LABEL, STATUSES, STATUS_LABEL } from "@/components/tickets/badges";
import { Button, Input, Select, Textarea } from "@/components/ui/primitives";
import { useTeams, useTicketTypes } from "@/hooks/use-workspace";

/**
 * Editor for the small `{kind, ...}` objects that rules (conditions, actions)
 * and macros (steps) are made of. The kinds and their fields come from the API
 * documentation for RuleIn / MacroIn.
 */
export type Item = Record<string, unknown> & { kind: string };

type FieldSpec =
  | { key: string; label: string; type: "text" | "textarea" | "words" | "bool" }
  | {
      key: string;
      label: string;
      type: "select" | "multi";
      options: { value: string; label: string }[];
    }
  | { key: string; label: string; type: "types" | "teams" };

export interface KindSpec {
  kind: string;
  label: string;
  fields: FieldSpec[];
  /** Starting values for a new item of this kind. */
  initial: Record<string, unknown>;
  /** At most one of these per list (macro reply / note). */
  single?: boolean;
}

const opts = (values: readonly string[], label?: (v: string) => string) =>
  values.map((v) => ({ value: v, label: label ? label(v) : v[0]!.toUpperCase() + v.slice(1) }));

const STATUS_OPTS = opts(STATUSES, (v) => STATUS_LABEL[v as keyof typeof STATUS_LABEL]);
const PRIORITY_OPTS = opts(PRIORITIES, (v) => PRIORITY_LABEL[v as keyof typeof PRIORITY_LABEL]);
const CHANNEL_OPTS = opts(["email", "web", "portal", "api", "chat", "phone"]);
const SENTIMENT_OPTS = opts(["negative", "neutral", "positive"]);

export const CONDITION_KINDS: KindSpec[] = [
  {
    kind: "text_contains_any",
    label: "Subject or customer message contains",
    fields: [{ key: "words", label: "Words (comma separated)", type: "words" }],
    initial: { words: [] },
  },
  {
    kind: "channel_in",
    label: "Channel is",
    fields: [{ key: "channels", label: "Channels", type: "multi", options: CHANNEL_OPTS }],
    initial: { channels: [] },
  },
  {
    kind: "to_address_contains",
    label: "Sent to an address containing",
    fields: [{ key: "text", label: "Text", type: "text" }],
    initial: { text: "" },
  },
  {
    kind: "status_in",
    label: "Status is",
    fields: [{ key: "statuses", label: "Statuses", type: "multi", options: STATUS_OPTS }],
    initial: { statuses: [] },
  },
  {
    kind: "sentiment_in",
    label: "Sentiment is",
    fields: [{ key: "sentiments", label: "Sentiments", type: "multi", options: SENTIMENT_OPTS }],
    initial: { sentiments: [] },
  },
];

export const RULE_ACTION_KINDS: KindSpec[] = [
  {
    kind: "set_type",
    label: "Set ticket type",
    fields: [
      { key: "type_id", label: "Type", type: "types" },
      { key: "only_if_empty", label: "Only if the ticket has no type yet", type: "bool" },
    ],
    initial: { type_id: "", only_if_empty: false },
  },
  {
    kind: "set_team",
    label: "Set team",
    fields: [{ key: "team_id", label: "Team", type: "teams" }],
    initial: { team_id: "" },
  },
  {
    kind: "set_priority",
    label: "Set priority",
    fields: [
      { key: "priority", label: "Priority", type: "select", options: PRIORITY_OPTS },
      { key: "keep_urgent", label: "Don't lower an urgent ticket", type: "bool" },
    ],
    initial: { priority: "high", keep_urgent: true },
  },
  {
    kind: "set_status",
    label: "Set status",
    fields: [{ key: "status", label: "Status", type: "select", options: STATUS_OPTS }],
    initial: { status: "open" },
  },
  {
    kind: "add_tag",
    label: "Add tag",
    fields: [{ key: "tag", label: "Tag", type: "text" }],
    initial: { tag: "" },
  },
];

export const MACRO_STEP_KINDS: KindSpec[] = [
  {
    kind: "reply",
    label: "Send a reply",
    single: true,
    fields: [
      { key: "text", label: "Reply (placeholders like {{customer.first_name}})", type: "textarea" },
    ],
    initial: { text: "" },
  },
  {
    kind: "note",
    label: "Add a private note",
    single: true,
    fields: [{ key: "text", label: "Note", type: "textarea" }],
    initial: { text: "" },
  },
  {
    kind: "set_status",
    label: "Set status",
    fields: [{ key: "status", label: "Status", type: "select", options: STATUS_OPTS }],
    initial: { status: "resolved" },
  },
  {
    kind: "set_priority",
    label: "Set priority",
    fields: [{ key: "priority", label: "Priority", type: "select", options: PRIORITY_OPTS }],
    initial: { priority: "low" },
  },
  {
    kind: "set_team",
    label: "Move to team (re-routes)",
    fields: [{ key: "team_id", label: "Team", type: "teams" }],
    initial: { team_id: "" },
  },
  {
    kind: "add_tag",
    label: "Add tag",
    fields: [{ key: "tag", label: "Tag", type: "text" }],
    initial: { tag: "" },
  },
];

/** Keeps what the user typed (commas and spaces included); the list is parsed as they type. */
function WordsInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const [text, setText] = useState(Array.isArray(value) ? value.join(", ") : "");
  return (
    <Input
      aria-label={label}
      value={text}
      placeholder="refund, chargeback"
      onChange={(e) => {
        setText(e.target.value);
        onChange(
          e.target.value
            .split(",")
            .map((w) => w.trim())
            .filter(Boolean),
        );
      }}
    />
  );
}

const checkbox = "size-4 accent-[var(--primary)]";

function FieldEditor({
  spec,
  value,
  onChange,
  label,
}: {
  spec: FieldSpec;
  value: unknown;
  onChange: (v: unknown) => void;
  label: string;
}) {
  const types = useTicketTypes();
  const teams = useTeams();
  const id = useId();

  switch (spec.type) {
    case "text":
      return (
        <Input
          aria-label={label}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "textarea":
      return (
        <Textarea
          aria-label={label}
          rows={4}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "words":
      return <WordsInput label={label} value={value} onChange={onChange} />;
    case "bool":
      return (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className={checkbox}
            checked={value === true}
            onChange={(e) => onChange(e.target.checked)}
          />
          {spec.label}
        </label>
      );
    case "select":
      return (
        <Select
          aria-label={label}
          value={String(value ?? "")}
          options={spec.options}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "multi": {
      const current = Array.isArray(value) ? (value as string[]) : [];
      return (
        <div role="group" aria-labelledby={id} className="flex flex-wrap gap-x-4 gap-y-1.5">
          <span id={id} className="sr-only">
            {label}
          </span>
          {spec.options.map((o) => (
            <label key={o.value} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                className={checkbox}
                checked={current.includes(o.value)}
                onChange={(e) =>
                  onChange(
                    e.target.checked ? [...current, o.value] : current.filter((v) => v !== o.value),
                  )
                }
              />
              {o.label}
            </label>
          ))}
        </div>
      );
    }
    case "types":
      return (
        <Select
          aria-label={label}
          placeholder="Choose a type…"
          value={String(value ?? "")}
          options={(types.data ?? []).map((t) => ({ value: t.id, label: t.name }))}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "teams":
      return (
        <Select
          aria-label={label}
          placeholder="Choose a team…"
          value={String(value ?? "")}
          options={(teams.data ?? []).map((t) => ({ value: t.id, label: t.name }))}
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }
}

export function ItemBuilder({
  title,
  addLabel,
  items,
  onChange,
  kinds,
  minItems = 0,
}: {
  title: string;
  addLabel: string;
  items: Item[];
  onChange: (items: Item[]) => void;
  kinds: KindSpec[];
  minItems?: number;
}) {
  const used = new Set(items.map((i) => i.kind));
  const available = kinds.filter((k) => !(k.single && used.has(k.kind)));

  const patch = (i: number, p: Record<string, unknown>) =>
    onChange(items.map((it, n) => (n === i ? { ...it, ...p } : it)));

  return (
    <fieldset className="space-y-2">
      <legend className="mb-1.5 text-[13px] font-medium text-foreground-2">{title}</legend>
      {items.map((it, i) => {
        const spec = kinds.find((k) => k.kind === it.kind);
        return (
          <div key={i} className="space-y-2 rounded-lg border border-border p-3">
            <div className="flex items-center gap-2">
              <div className="min-w-0 flex-1">
                <Select
                  aria-label={`${title} ${i + 1} type`}
                  value={it.kind}
                  options={kinds
                    .filter((k) => k.kind === it.kind || !(k.single && used.has(k.kind)))
                    .map((k) => ({ value: k.kind, label: k.label }))}
                  onChange={(e) => {
                    const next = kinds.find((k) => k.kind === e.target.value);
                    if (next)
                      onChange(
                        items.map((x, n) => (n === i ? { kind: next.kind, ...next.initial } : x)),
                      );
                  }}
                />
              </div>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove ${title.toLowerCase()} ${i + 1}`}
                disabled={items.length <= minItems}
                onClick={() => onChange(items.filter((_, n) => n !== i))}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
            {spec?.fields.map((f) => (
              <FieldEditor
                key={f.key}
                spec={f}
                label={f.label}
                value={it[f.key]}
                onChange={(v) => patch(i, { [f.key]: v })}
              />
            ))}
            {!spec && (
              <p className="text-xs text-muted-foreground">
                Unknown step “{it.kind}”. It is kept as is.
              </p>
            )}
          </div>
        );
      })}
      <Button
        size="sm"
        variant="outline"
        disabled={available.length === 0}
        onClick={() => {
          const k = available[0];
          if (k) onChange([...items, { kind: k.kind, ...k.initial }]);
        }}
      >
        <Plus className="size-3.5" /> {addLabel}
      </Button>
    </fieldset>
  );
}

/** One-line summary of an item, for lists. */
export function summarize(it: Item, kinds: KindSpec[]): string {
  const spec = kinds.find((k) => k.kind === it.kind);
  const label = spec?.label ?? it.kind;
  const bits = (spec?.fields ?? [])
    .filter((f) => f.type !== "bool" && f.type !== "types" && f.type !== "teams")
    .map((f) => {
      const v = it[f.key];
      if (Array.isArray(v)) return v.join(", ");
      return typeof v === "string" ? v : "";
    })
    .filter(Boolean);
  return bits.length ? `${label}: ${bits.join(", ")}` : label;
}

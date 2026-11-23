"use client";

import { Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";

import { PRIORITIES, PRIORITY_LABEL, STATUSES, STATUS_LABEL } from "@/components/tickets/badges";
import { Button, Input, Select, Textarea } from "@/components/ui/primitives";
import {
  type CatalogRef,
  ContactFieldEditor,
  DurationInput,
  EmailBodyField,
  MemberSelect,
  OP_LABEL,
  SegmentsPicker,
  useCatalogEnum,
  WebhookSelect,
  YesNo,
} from "@/components/workspace/automation-fields";
import { useTeams, useTicketTypes } from "@/hooks/use-workspace";
import { formatMinutes } from "@/lib/automation/graph";

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
      /** Prefer the values the server catalog lists for this parameter, when it has loaded. */
      catalog?: CatalogRef;
    }
  | { key: string; label: string; type: "types" | "teams" | "members" | "webhooks" | "segments" }
  | { key: string; label: string; type: "emailbody" }
  | { key: string; label: string; type: "yesno"; yes?: string; no?: string }
  | { key: string; label: string; type: "duration"; min: number; max?: number }
  /** Edits several keys of the item at once (contact_field: key, op, value). */
  | { key: string; label: string; type: "contactField" };

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
const SLA_STATE_OPTS = [
  { value: "on_track", label: "On track" },
  { value: "close", label: "Close to breach" },
  { value: "breached", label: "Breached" },
];

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
  {
    kind: "sla_in",
    label: "SLA is",
    fields: [{ key: "states", label: "SLA states", type: "multi", options: SLA_STATE_OPTS }],
    initial: { states: ["breached"] },
  },
];

const TIME_OF_OPTS = [
  { value: "customer_reply", label: "the last customer reply" },
  { value: "agent_reply", label: "the last agent reply" },
];
const TIME_OP_OPTS = [
  { value: "more_than", label: "is more than" },
  { value: "less_than", label: "is less than" },
];

/** Phase 5 conditions, on top of the original six. */
const NEW_CONDITION_KINDS: KindSpec[] = [
  {
    kind: "contact_field",
    label: "Contact field",
    fields: [{ key: "key", label: "Contact field", type: "contactField" }],
    initial: { key: "", op: "equals", value: "" },
  },
  {
    kind: "contact_vip",
    label: "Customer is VIP",
    fields: [{ key: "value", label: "Customer is VIP", type: "yesno" }],
    initial: { value: true },
  },
  {
    kind: "contact_in_segment",
    label: "Customer is in segment",
    fields: [
      { key: "negate", label: "Customer", type: "yesno", yes: "Is not in", no: "Is in" },
      { key: "segment_ids", label: "Segments (at most 10)", type: "segments" },
    ],
    initial: { segment_ids: [], negate: false },
  },
  {
    kind: "time_since",
    label: "Time since the last reply",
    fields: [
      {
        key: "of",
        label: "Time since",
        type: "select",
        options: TIME_OF_OPTS,
        catalog: { group: "conditions", kind: "time_since", key: "of" },
      },
      {
        key: "op",
        label: "Compared with",
        type: "select",
        options: TIME_OP_OPTS,
        catalog: { group: "conditions", kind: "time_since", key: "op" },
      },
      { key: "minutes", label: "Time", type: "duration", min: 1 },
    ],
    initial: { of: "customer_reply", op: "more_than", minutes: 1440 },
  },
  {
    kind: "snoozed",
    label: "Ticket is snoozed",
    fields: [{ key: "value", label: "Ticket is snoozed", type: "yesno" }],
    initial: { value: true },
  },
  {
    kind: "has_followers",
    label: "Ticket has followers",
    fields: [{ key: "value", label: "Ticket has followers", type: "yesno" }],
    initial: { value: true },
  },
];

/** Phase 5 actions, on top of the original five. `call_webhook` needs `manage_access`. */
const NEW_ACTION_KINDS: KindSpec[] = [
  {
    kind: "snooze",
    label: "Snooze for",
    fields: [{ key: "minutes", label: "Snooze for", type: "duration", min: 1, max: 525_600 }],
    initial: { minutes: 1440 },
  },
  {
    kind: "add_follower",
    label: "Add follower",
    fields: [{ key: "member_id", label: "Member", type: "members" }],
    initial: { member_id: "" },
  },
  {
    kind: "send_email",
    label: "Send email to the customer",
    fields: [{ key: "body", label: "Email", type: "emailbody" }],
    initial: { body: "" },
  },
  {
    kind: "call_webhook",
    label: "Call a webhook",
    fields: [{ key: "webhook_id", label: "Webhook", type: "webhooks" }],
    initial: { webhook_id: "" },
  },
];

/**
 * Conditions a rule can use for its event. A new ticket has no SLA state to check yet,
 * so `sla_in` is only offered on the other events.
 */
export function conditionKindsFor(event: string): KindSpec[] {
  return event === "created" ? CONDITION_KINDS.filter((k) => k.kind !== "sla_in") : CONDITION_KINDS;
}

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

/** Every condition rules and workflows know about. */
export const AUTOMATION_CONDITION_KINDS: KindSpec[] = [...CONDITION_KINDS, ...NEW_CONDITION_KINDS];
/** Every action rules and workflows know about. */
export const AUTOMATION_ACTION_KINDS: KindSpec[] = [...RULE_ACTION_KINDS, ...NEW_ACTION_KINDS];

/** Conditions for a trigger. A new ticket has no SLA state to check yet, so `sla_in` is left out. */
export function automationConditionKinds(event: string): KindSpec[] {
  return event === "created"
    ? AUTOMATION_CONDITION_KINDS.filter((k) => k.kind !== "sla_in")
    : AUTOMATION_CONDITION_KINDS;
}

/** Actions someone may pick. Calling a webhook is an Admin thing, so it is hidden from others. */
export function automationActionKinds(canCallWebhook: boolean): KindSpec[] {
  return canCallWebhook
    ? AUTOMATION_ACTION_KINDS
    : AUTOMATION_ACTION_KINDS.filter((k) => k.kind !== "call_webhook");
}

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

/** A select whose choices the server catalog may refine (it knows the exact allowed values). */
function CatalogSelect({
  spec,
  value,
  label,
  onChange,
}: {
  spec: Extract<FieldSpec, { type: "select" | "multi" }>;
  value: unknown;
  label: string;
  onChange: (v: unknown) => void;
}) {
  const allowed = useCatalogEnum(spec.catalog);
  const options = allowed
    ? allowed.map((v) => ({
        value: v,
        label:
          spec.options.find((o) => o.value === v)?.label ?? OP_LABEL[v] ?? v.replaceAll("_", " "),
      }))
    : spec.options;
  return (
    <Select
      aria-label={label}
      value={String(value ?? "")}
      options={options}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function FieldEditor({
  spec,
  item,
  patch,
  label,
}: {
  spec: FieldSpec;
  item: Item;
  patch: (p: Record<string, unknown>) => void;
  label: string;
}) {
  const types = useTicketTypes();
  const teams = useTeams();
  const id = useId();
  const value = item[spec.key];
  const onChange = (v: unknown) => patch({ [spec.key]: v });

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
      return <CatalogSelect spec={spec} value={value} label={label} onChange={onChange} />;
    case "yesno":
      return (
        <YesNo
          label={label}
          value={value}
          yes={spec.yes}
          no={spec.no}
          onChange={(v) => onChange(v)}
        />
      );
    case "duration":
      return (
        <DurationInput
          label={label}
          minutes={value}
          min={spec.min}
          max={spec.max}
          onChange={(m) => onChange(m)}
        />
      );
    case "members":
      return <MemberSelect label={label} value={value} onChange={onChange} />;
    case "webhooks":
      return <WebhookSelect label={label} value={value} onChange={onChange} />;
    case "segments":
      return <SegmentsPicker label={label} value={value} onChange={onChange} />;
    case "emailbody":
      return <EmailBodyField label={label} value={value} onChange={onChange} />;
    case "contactField":
      return <ContactFieldEditor item={item} patch={patch} />;
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
  maxItems,
}: {
  title: string;
  addLabel: string;
  items: Item[];
  onChange: (items: Item[]) => void;
  kinds: KindSpec[];
  minItems?: number;
  maxItems?: number;
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
                item={it}
                patch={(p) => patch(i, p)}
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
        disabled={available.length === 0 || (maxItems !== undefined && items.length >= maxItems)}
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

/** Names for the contact fields, so a `contact_field` condition can say which field. */
export type FieldNames = { key: string; label: string }[];

const labelOf = (opts: { value: string; label: string }[], v: unknown) =>
  opts.find((o) => o.value === v)?.label;

/** One-line summary of an item, for lists. */
export function summarize(it: Item, kinds: KindSpec[], contactFields?: FieldNames): string {
  const spec = kinds.find((k) => k.kind === it.kind);
  const label = spec?.label ?? it.kind;

  if (it.kind === "time_since") {
    const of = labelOf(TIME_OF_OPTS, it.of) ?? "the last reply";
    const op = labelOf(TIME_OP_OPTS, it.op) ?? "";
    const mins = typeof it.minutes === "number" && it.minutes > 0 ? formatMinutes(it.minutes) : "";
    return `Time since ${of} ${op} ${mins}`.replace(/\s+/g, " ").trim();
  }
  if (it.kind === "contact_field") {
    const key = typeof it.key === "string" ? it.key : "";
    if (!key) return label;
    const name = contactFields?.find((f) => f.key === key)?.label ?? key;
    const op = OP_LABEL[String(it.op ?? "equals")] ?? String(it.op);
    const noValue = it.op === "is_set" || it.op === "is_not_set";
    const value = noValue || it.value == null || it.value === "" ? "" : String(it.value);
    return `${name} ${op} ${value}`.trim();
  }

  const bits = (spec?.fields ?? [])
    .filter((f) => ["text", "textarea", "words", "multi", "select", "duration"].includes(f.type))
    .map((f) => {
      const v = it[f.key];
      if (f.type === "duration") return typeof v === "number" && v > 0 ? formatMinutes(v) : "";
      const options = "options" in f ? f.options : [];
      if (Array.isArray(v)) return v.map((x) => labelOf(options, x) ?? String(x)).join(", ");
      return typeof v === "string" ? (labelOf(options, v) ?? v) : "";
    })
    .filter(Boolean);
  return bits.length ? `${label}: ${bits.join(", ")}` : label;
}

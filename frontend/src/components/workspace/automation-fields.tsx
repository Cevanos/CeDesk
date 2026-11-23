"use client";

import { useId, useState } from "react";

import { DatePicker } from "@/components/ui/date-picker";
import { Input, Select, Textarea } from "@/components/ui/primitives";
import { useContactFields, useSegments } from "@/hooks/use-contacts";
import { useAutomationCatalog, useMemberOptions, useWebhooks } from "@/hooks/use-workspace";
import type { FieldKind } from "@/lib/api/contacts-types";
import { splitMinutes, toMinutes, type DurationUnit } from "@/lib/automation/graph";

const checkbox = "size-4 accent-[var(--primary)]";

/** The placeholders an automatic email can use (the same as saved replies). */
const EMAIL_PLACEHOLDERS = [
  "{{customer.first_name}}",
  "{{customer.name}}",
  "{{agent.name}}",
  "{{fields.<key>}}",
];

/* ---------- catalog-driven choices ---------- */

export interface CatalogRef {
  group: "conditions" | "actions";
  kind: string;
  key: string;
}

/** The allowed values of one parameter, as the server's catalog lists them (when it has loaded). */
export function useCatalogEnum(ref: CatalogRef | undefined): string[] | null {
  const catalog = useAutomationCatalog();
  if (!ref) return null;
  const entry = catalog.data?.[ref.group]?.find((e) => e.kind === ref.kind);
  const values = entry?.params?.properties?.[ref.key]?.enum;
  return Array.isArray(values) ? values.map(String) : null;
}

/* ---------- yes / no ---------- */

export function YesNo({
  label,
  value,
  onChange,
  yes = "Yes",
  no = "No",
}: {
  label: string;
  value: unknown;
  onChange: (v: boolean) => void;
  yes?: string;
  no?: string;
}) {
  return (
    <Select
      aria-label={label}
      value={value === false ? "no" : "yes"}
      options={[
        { value: "yes", label: yes },
        { value: "no", label: no },
      ]}
      onChange={(e) => onChange(e.target.value === "yes")}
    />
  );
}

/* ---------- duration ---------- */

const UNIT_OPTIONS: { value: DurationUnit; label: string }[] = [
  { value: "minutes", label: "Minutes" },
  { value: "hours", label: "Hours" },
  { value: "days", label: "Days" },
];

/** A duration the API stores in minutes, edited as a number and a unit. */
export function DurationInput({
  label,
  minutes,
  onChange,
  min = 1,
  max,
}: {
  label: string;
  minutes: unknown;
  onChange: (minutes: number) => void;
  min?: number;
  max?: number;
}) {
  const start = splitMinutes(typeof minutes === "number" ? minutes : min);
  const [text, setText] = useState(String(start.value));
  const [unit, setUnit] = useState<DurationUnit>(start.unit);
  const current = typeof minutes === "number" ? minutes : NaN;
  const out = Number.isFinite(current) && (current < min || (max !== undefined && current > max));

  const emit = (t: string, u: DurationUnit) => {
    const n = Number(t);
    onChange(t.trim() !== "" && Number.isFinite(n) ? toMinutes(n, u) : 0);
  };
  return (
    <div>
      <div className="flex gap-2">
        <Input
          type="number"
          min={1}
          step="any"
          inputMode="decimal"
          aria-label={`${label} (amount)`}
          aria-invalid={out || undefined}
          className="w-28"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            emit(e.target.value, unit);
          }}
        />
        <div className="w-36">
          <Select
            aria-label={`${label} (unit)`}
            value={unit}
            options={UNIT_OPTIONS}
            onChange={(e) => {
              const u = e.target.value as DurationUnit;
              setUnit(u);
              emit(text, u);
            }}
          />
        </div>
      </div>
      {out && (
        <p role="alert" className="mt-1 text-xs text-danger">
          {max !== undefined
            ? `Use between ${splitLabel(min)} and ${splitLabel(max)}.`
            : `Use at least ${splitLabel(min)}.`}
        </p>
      )}
    </div>
  );
}

function splitLabel(minutes: number) {
  const { value, unit } = splitMinutes(minutes);
  return `${value} ${value === 1 ? unit.slice(0, -1) : unit}`;
}

/* ---------- pickers ---------- */

export function MemberSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (v: string) => void;
}) {
  const members = useMemberOptions();
  const options = (members.data ?? [])
    .filter((m) => m.is_active)
    .map((m) => ({ value: m.id, label: m.name }));
  return (
    <Select
      aria-label={label}
      placeholder="Choose a member…"
      value={String(value ?? "")}
      options={options}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/** Webhooks are an Admin area: this only mounts for someone who can use it. */
export function WebhookSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (v: string) => void;
}) {
  const hooks = useWebhooks();
  const options = (hooks.data ?? []).map((w) => ({ value: w.id, label: w.url }));
  return (
    <Select
      aria-label={label}
      placeholder="Choose a webhook…"
      value={String(value ?? "")}
      options={options}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

const MAX_SEGMENTS = 10;

export function SegmentsPicker({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (v: string[]) => void;
}) {
  const segments = useSegments();
  const id = useId();
  const picked = Array.isArray(value) ? (value as string[]) : [];
  const list = segments.data ?? [];
  if (!segments.isPending && list.length === 0) {
    return <p className="text-xs text-muted-foreground">There are no segments yet.</p>;
  }
  return (
    <div role="group" aria-labelledby={id} className="flex flex-wrap gap-x-4 gap-y-1.5">
      <span id={id} className="sr-only">
        {label}
      </span>
      {list.map((s) => {
        const on = picked.includes(s.id);
        return (
          <label key={s.id} className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              className={checkbox}
              checked={on}
              disabled={!on && picked.length >= MAX_SEGMENTS}
              onChange={(e) =>
                onChange(e.target.checked ? [...picked, s.id] : picked.filter((x) => x !== s.id))
              }
            />
            {s.name}
          </label>
        );
      })}
      {picked.length >= MAX_SEGMENTS && (
        <p className="w-full text-xs text-muted-foreground">At most {MAX_SEGMENTS} segments.</p>
      )}
    </div>
  );
}

/* ---------- email body ---------- */

export function EmailBodyField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (v: string) => void;
}) {
  const [area, setArea] = useState<HTMLTextAreaElement | null>(null);
  const text = String(value ?? "");
  const insert = (token: string) => {
    const start = area?.selectionStart ?? text.length;
    const end = area?.selectionEnd ?? text.length;
    onChange(text.slice(0, start) + token + text.slice(end));
    requestAnimationFrame(() => {
      area?.focus();
      area?.setSelectionRange(start + token.length, start + token.length);
    });
  };
  return (
    <div className="space-y-1.5">
      <Textarea
        ref={setArea}
        aria-label={label}
        rows={4}
        maxLength={5000}
        value={text}
        onChange={(e) => onChange(e.target.value)}
      />
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-muted-foreground">Insert:</span>
        {EMAIL_PLACEHOLDERS.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => insert(p)}
            className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-foreground-2 hover:bg-surface-3"
          >
            {p}
          </button>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        Sent as a reply from the workspace, 1 to 5000 characters. {text.length}/5000
      </p>
    </div>
  );
}

/* ---------- contact field condition ---------- */

export const OP_LABEL: Record<string, string> = {
  equals: "equals",
  not_equals: "does not equal",
  is_set: "is set",
  is_not_set: "is not set",
  contains: "contains",
  more_than: "is more than",
  less_than: "is less than",
};

const NO_VALUE_OPS = new Set(["is_set", "is_not_set"]);

/** Which operators make sense for a kind of field. */
export function opsForKind(kind: FieldKind | undefined): string[] {
  const base = ["equals", "not_equals", "is_set", "is_not_set"];
  switch (kind) {
    case "text":
      return [...base, "contains"];
    case "number":
    case "date":
      return [...base, "more_than", "less_than"];
    default:
      return base;
  }
}

export function ContactFieldEditor({
  item,
  patch,
}: {
  item: Record<string, unknown>;
  patch: (p: Record<string, unknown>) => void;
}) {
  const fields = useContactFields();
  const allowed = useCatalogEnum({ group: "conditions", kind: "contact_field", key: "op" });
  const field = (fields.data ?? []).find((f) => f.key === item.key);
  const kind = field?.kind;
  const op = String(item.op ?? "equals");
  const ops = opsForKind(kind).filter((o) => !allowed || allowed.includes(o));
  const needsValue = !NO_VALUE_OPS.has(op);

  const resetFor = (key: string) => {
    const next = (fields.data ?? []).find((f) => f.key === key);
    const nextOps = opsForKind(next?.kind);
    patch({
      key,
      op: nextOps.includes(op) ? op : "equals",
      value: next?.kind === "checkbox" ? true : "",
    });
  };

  return (
    <div className="space-y-2">
      <Select
        aria-label="Contact field"
        placeholder="Choose a field…"
        value={String(item.key ?? "")}
        options={(fields.data ?? []).map((f) => ({ value: f.key, label: f.label }))}
        onChange={(e) => resetFor(e.target.value)}
      />
      {fields.data && fields.data.length === 0 && (
        <p className="text-xs text-muted-foreground">There are no contact fields yet.</p>
      )}
      <Select
        aria-label="Contact field operator"
        value={op}
        options={ops.map((o) => ({ value: o, label: OP_LABEL[o] ?? o }))}
        onChange={(e) => patch({ op: e.target.value })}
      />
      {needsValue && (
        <ContactValue
          kind={kind}
          options={field?.options ?? []}
          value={item.value}
          onChange={(v) => patch({ value: v })}
        />
      )}
    </div>
  );
}

function ContactValue({
  kind,
  options,
  value,
  onChange,
}: {
  kind: FieldKind | undefined;
  options: string[];
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  switch (kind) {
    case "number":
      return (
        <Input
          type="number"
          step="any"
          aria-label="Value"
          value={typeof value === "number" ? value : ""}
          onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
        />
      );
    case "date":
      return (
        <DatePicker
          type="date"
          aria-label="Value"
          value={String(value ?? "")}
          onChange={(v) => onChange(v)}
        />
      );
    case "select":
      return (
        <Select
          aria-label="Value"
          placeholder="Choose a value…"
          value={String(value ?? "")}
          options={options.map((o) => ({ value: o, label: o }))}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "checkbox":
      return (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className={checkbox}
            checked={value === true}
            onChange={(e) => onChange(e.target.checked)}
          />
          Checked
        </label>
      );
    default:
      return (
        <Input
          aria-label="Value"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }
}

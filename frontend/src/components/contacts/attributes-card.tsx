"use client";

import { SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { DatePicker } from "@/components/ui/date-picker";
import { Card, Input, Select } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { useSetAttributes } from "@/hooks/use-contacts";
import { ApiError } from "@/lib/api/client";
import type { AttributeValue, ContactDetail, ContactFieldRef } from "@/lib/api/contacts-types";
import { showValue } from "@/lib/contacts";
import { hasAction } from "@/lib/permissions";
import { cn } from "@/lib/utils";

/** What the box holds as text, and what goes to the server when it is saved. */
function toText(v: AttributeValue | undefined): string {
  return v === null || v === undefined ? "" : String(v);
}

function parse(field: ContactFieldRef, text: string): AttributeValue | "invalid" {
  const t = text.trim();
  if (t === "") return null;
  if (field.kind === "number") {
    const n = Number(t);
    return Number.isFinite(n) ? n : "invalid";
  }
  return t;
}

function AttributeRow({
  contact,
  field,
  canEdit,
}: {
  contact: ContactDetail;
  field: ContactFieldRef;
  canEdit: boolean;
}) {
  const set = useSetAttributes(contact.id);
  const value = contact.attributes[field.key];
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const save = (v: AttributeValue) =>
    set.mutate(
      { [field.key]: v },
      {
        onSuccess: () => setEditing(false),
        onError: (e) => toast.error(e instanceof ApiError ? e.userMessage : "Could not save."),
      },
    );

  const commit = () => {
    const v = parse(field, draft);
    if (v === "invalid") return toast.error(`${field.label} must be a number.`);
    if (v === (value ?? null)) return setEditing(false);
    save(v);
  };

  let control: React.ReactNode;
  if (field.kind === "checkbox") {
    control = (
      <input
        type="checkbox"
        aria-label={field.label}
        className="size-4 accent-primary"
        checked={value === true}
        disabled={!canEdit || set.isPending}
        onChange={(e) => save(e.target.checked)}
      />
    );
  } else if (editing && canEdit) {
    const common = {
      "aria-label": field.label,
      onKeyDown: (e: React.KeyboardEvent) => {
        if (e.key === "Escape") setEditing(false);
        if (e.key === "Enter") commit();
      },
    };
    control =
      field.kind === "select" ? (
        <Select
          aria-label={field.label}
          value={draft}
          defaultOpen
          onChange={(e) => save(e.target.value || null)}
          onBlur={() => setEditing(false)}
        >
          <option value="">—</option>
          {(field.options ?? []).map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </Select>
      ) : field.kind === "date" ? (
        <DatePicker
          aria-label={field.label}
          defaultOpen
          className="h-8"
          value={draft}
          onChange={(v) => save(v || null)}
          onClose={() => setEditing(false)}
        />
      ) : (
        <Input
          {...common}
          // The user clicked the value to edit it, so moving focus here is expected.
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus
          className="h-8"
          type={field.kind === "number" ? "number" : "text"}
          maxLength={2000}
          value={draft}
          disabled={set.isPending}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
        />
      );
  } else {
    const text = showValue(value);
    control = canEdit ? (
      <button
        type="button"
        className={cn(
          "max-w-full truncate rounded-md px-1.5 py-0.5 text-right hover:bg-surface-2",
          text === "—" && "text-muted-foreground",
        )}
        aria-label={`Edit ${field.label}`}
        onClick={() => {
          setDraft(toText(value));
          setEditing(true);
        }}
      >
        {text}
      </button>
    ) : (
      <span className={cn("px-1.5", text === "—" && "text-muted-foreground")}>{text}</span>
    );
  }

  return (
    <>
      <dt className="pt-0.5 text-muted-foreground">{field.label}</dt>
      <dd className="flex min-w-0 justify-end">{control}</dd>
    </>
  );
}

/** The customer's contact fields. Agents who may change fields edit a value by clicking it. */
export function AttributesCard({
  contact,
  className,
}: {
  contact: ContactDetail;
  className?: string;
}) {
  const { user } = useCurrentUser();
  const canEdit = hasAction(user, "change_fields") && !contact.erased_at;
  if (contact.fields.length === 0) return null;
  return (
    <Card className={cn("rounded-2xl p-4 sm:p-5", className)}>
      <h3 className="mb-3 flex items-center gap-2 text-[11px] font-bold tracking-[0.08em] text-muted-foreground uppercase after:h-px after:flex-1 after:bg-border">
        <SlidersHorizontal className="size-3.5" /> Attributes
      </h3>
      <dl className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1.5 text-sm">
        {contact.fields.map((f) => (
          <AttributeRow
            key={`${f.key}:${String(contact.attributes[f.key] ?? "")}`}
            contact={contact}
            field={f}
            canEdit={canEdit}
          />
        ))}
      </dl>
    </Card>
  );
}

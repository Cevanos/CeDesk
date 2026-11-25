"use client";

import { ArrowDown, ArrowUp, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { Dialog } from "@/components/ui/overlay";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
} from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import {
  useDeleteTicketType,
  useReorderTicketTypes,
  useSaveTicketType,
  useTicketTypes,
} from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { TicketType, TicketTypeField } from "@/lib/api/types";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";

const slugKey = (v: string) =>
  v
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "");

const typeSchema = yup.object({
  name: yup.string().trim().required("Enter a name."),
  fields: yup
    .array()
    .max(30, "A type can have at most 30 fields.")
    .test("labels", "Every field needs a label.", (list) =>
      (list ?? []).every((f) => !!f?.label?.trim() && !!slugKey(f.key || f.label || "")),
    ),
});

export function TicketTypesView() {
  const types = useTicketTypes();
  const del = useDeleteTicketType();
  const reorder = useReorderTicketTypes();
  const { ask, dialog } = useConfirm();
  const [editing, setEditing] = useState<TicketType | "new" | null>(null);

  const move = (i: number, dir: -1 | 1) => {
    const ids = (types.data ?? []).map((t) => t.id);
    const j = i + dir;
    if (j < 0 || j >= ids.length) return;
    const a = ids[i];
    const b = ids[j];
    if (a === undefined || b === undefined) return;
    ids[i] = b;
    ids[j] = a;
    reorder.mutate(ids);
  };

  return (
    <div>
      {dialog}
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Each type can ask for its own fields (up to 30).
        </p>
        <Button onClick={() => setEditing("new")}>
          <Plus className="size-4" /> Add type
        </Button>
      </div>

      {types.isPending ? (
        <Skeleton className="h-32" />
      ) : types.isError ? (
        <EmptyState title="We couldn't load ticket types">
          {types.error instanceof ApiError ? types.error.userMessage : ""}
        </EmptyState>
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-border">
            {types.data.map((t, i) => (
              <li key={t.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="flex flex-col">
                  <button
                    aria-label={`Move ${t.name} up`}
                    disabled={i === 0 || reorder.isPending}
                    onClick={() => move(i, -1)}
                    className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                  >
                    <ArrowUp className="size-4" />
                  </button>
                  <button
                    aria-label={`Move ${t.name} down`}
                    disabled={i === types.data.length - 1 || reorder.isPending}
                    onClick={() => move(i, 1)}
                    className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                  >
                    <ArrowDown className="size-4" />
                  </button>
                </div>
                <div className="min-w-0 flex-1 basis-40">
                  <div className="font-medium">{t.name}</div>
                  <div className="text-xs text-muted-foreground">{t.fields.length} fields</div>
                </div>
                {t.for_customers && <Badge tone="success">Customers can pick</Badge>}
                {t.required_skill && <Badge tone="primary">Skill: {t.required_skill}</Badge>}
                <Button size="sm" variant="outline" onClick={() => setEditing(t)}>
                  <Pencil className="size-3.5" /> Edit
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={del.isPending}
                  onClick={() =>
                    ask({
                      title: "Delete this ticket type?",
                      message: "This can't be undone.",
                      detail: t.name,
                      confirmLabel: "Delete",
                      run: () =>
                        del.mutate(t.id, { onSuccess: () => toast.success("Type deleted") }),
                    })
                  }
                >
                  <Trash2 className="size-3.5" /> Delete
                </Button>
              </li>
            ))}
            {types.data.length === 0 && (
              <li className="p-6 text-center text-sm text-muted-foreground">
                No ticket types yet.
              </li>
            )}
          </ul>
        </Card>
      )}

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "Add ticket type" : "Edit ticket type"}
        className="sm:max-w-xl"
      >
        {editing !== null && (
          <TypeForm
            key={editing === "new" ? "new" : editing.id}
            type={editing === "new" ? null : editing}
            onClose={() => setEditing(null)}
          />
        )}
      </Dialog>
    </div>
  );
}

const blank = (): TicketTypeField => ({ key: "", label: "", kind: "text", required: false });

function TypeForm({ type, onClose }: { type: TicketType | null; onClose: () => void }) {
  const save = useSaveTicketType();
  const [error, setError] = useState<string | null>(null);
  const form = useFormik({
    initialValues: {
      name: type?.name ?? "",
      skill: type?.required_skill ?? "",
      for_customers: type?.for_customers ?? false,
      fields: (type?.fields ?? []) as TicketTypeField[],
    },
    validationSchema: typeSchema,
    onSubmit: (v) => {
      const clean = v.fields.map((f) => ({
        ...f,
        key: slugKey(f.key || f.label),
        label: f.label.trim(),
        options:
          f.kind === "select" ? (f.options ?? []).map((o) => o.trim()).filter(Boolean) : undefined,
      }));
      setError(null);
      save.mutate(
        {
          id: type?.id,
          name: v.name.trim(),
          fields: clean,
          required_skill: v.skill.trim() || null,
          for_customers: v.for_customers,
        },
        {
          onSuccess: () => {
            toast.success("Ticket type saved");
            onClose();
          },
          onError: (e) =>
            e instanceof ApiError &&
            e.status === 422 &&
            setError(e.details.map((d) => d.message).join(" ")),
        },
      );
    },
  });
  const fields = form.values.fields;
  const setFields = (fn: (f: TicketTypeField[]) => TicketTypeField[]) =>
    form.setFieldValue("fields", fn(form.values.fields));
  const patch = (i: number, p: Partial<TicketTypeField>) =>
    setFields((f) => f.map((x, j) => (j === i ? { ...x, ...p } : x)));
  const fieldsError =
    form.submitCount > 0 && typeof form.errors.fields === "string" ? form.errors.fields : null;

  return (
    <form noValidate className="space-y-4" onSubmit={form.handleSubmit}>
      {(error ?? fieldsError) && (
        <div role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
          {error ?? fieldsError}
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" htmlFor="tt-name" error={fieldError(form, "name")}>
          <Input id="tt-name" {...bind(form, "name")} />
        </Field>
        <Field
          label="Required skill (optional)"
          htmlFor="tt-skill"
          hint="Only agents with this skill get it automatically."
        >
          <Input id="tt-skill" {...bind(form, "skill")} />
        </Field>
      </div>

      <div className="flex items-center justify-between gap-4 rounded-lg border border-border p-3 text-sm">
        <div>
          <p id="tt-customers" className="font-medium">
            Customers can pick this
          </p>
          <p id="tt-customers-d" className="text-xs text-muted-foreground">
            Show this type on the customer portal and the web form.
          </p>
        </div>
        <Switch
          checked={form.values.for_customers}
          onChange={(v) => form.setFieldValue("for_customers", v)}
          labelledBy="tt-customers"
          describedBy="tt-customers-d"
        />
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[13px] font-medium text-foreground-2">Fields</span>
          <Button
            size="sm"
            variant="outline"
            disabled={fields.length >= 30}
            onClick={() => setFields((f) => [...f, blank()])}
          >
            <Plus className="size-3.5" /> Add field
          </Button>
        </div>
        <div className="space-y-2.5">
          {fields.map((f, i) => (
            <div
              key={i}
              className="grid gap-2 rounded-lg border border-border p-3 sm:grid-cols-[1fr_8rem_auto]"
            >
              <Input
                aria-label="Field label"
                placeholder="Label, e.g. Order ID"
                value={f.label}
                onChange={(e) => patch(i, { label: e.target.value })}
              />
              <Select
                aria-label="Field kind"
                value={f.kind}
                onChange={(e) => patch(i, { kind: e.target.value as TicketTypeField["kind"] })}
              >
                <option value="text">Text</option>
                <option value="number">Number</option>
                <option value="select">Select</option>
              </Select>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-1.5 text-sm">
                  <input
                    type="checkbox"
                    className="size-4 accent-[var(--primary)]"
                    checked={!!f.required}
                    onChange={(e) => patch(i, { required: e.target.checked })}
                  />{" "}
                  Required
                </label>
                <button
                  type="button"
                  aria-label="Remove field"
                  onClick={() => setFields((x) => x.filter((_, j) => j !== i))}
                  className="text-muted-foreground hover:text-danger"
                >
                  <Trash2 className="size-4" />
                </button>
              </div>
              {f.kind === "select" && (
                <Input
                  className="sm:col-span-3"
                  aria-label="Options"
                  placeholder="Options, comma separated"
                  value={(f.options ?? []).join(", ")}
                  onChange={(e) => patch(i, { options: e.target.value.split(",") })}
                />
              )}
            </div>
          ))}
          {fields.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No fields. Tickets of this type just use the standard ones.
            </p>
          )}
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          Save type
        </Button>
      </div>
    </form>
  );
}

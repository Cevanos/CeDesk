"use client";

import { Pencil, Plus, SlidersHorizontal, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { Dialog } from "@/components/ui/overlay";
import {
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
  Textarea,
} from "@/components/ui/primitives";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import { useCurrentUser } from "@/hooks/use-auth";
import { useContactFields, useDeleteContactField, useSaveContactField } from "@/hooks/use-contacts";
import { ApiError } from "@/lib/api/client";
import type { ContactField, FieldKind } from "@/lib/api/contacts-types";
import { keyFromLabel } from "@/lib/contacts";
import { bind, bindSelect, fieldError, useFormik, yup } from "@/lib/forms";
import { hasAction } from "@/lib/permissions";

const splitOptions = (v?: string) =>
  (v ?? "")
    .split("\n")
    .map((o) => o.trim())
    .filter(Boolean);

const KINDS: { value: FieldKind; label: string }[] = [
  { value: "text", label: "Text" },
  { value: "number", label: "Number" },
  { value: "date", label: "Date" },
  { value: "select", label: "Dropdown" },
  { value: "checkbox", label: "Checkbox" },
];
const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.value, k.label]));
const KEY_RE = /^[a-z][a-z0-9_]*$/;

export function ContactFieldsView() {
  const { user } = useCurrentUser();
  const allowed = hasAction(user, "manage_contacts", user?.role === "admin");
  const fields = useContactFields();
  const del = useDeleteContactField();
  const { ask, dialog } = useConfirm();
  const [editing, setEditing] = useState<ContactField | "new" | null>(null);

  if (user && !allowed) {
    return (
      <Card>
        <EmptyState title="Not available">You don&apos;t have access to contact fields.</EmptyState>
      </Card>
    );
  }

  return (
    <div>
      {dialog}
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Extra details to keep on every customer, like a plan or an account number. Agents fill the
          values in on the customer page.
        </p>
        <Button onClick={() => setEditing("new")}>
          <Plus className="size-4" /> Add field
        </Button>
      </div>

      {fields.isPending ? (
        <Skeleton className="h-32" />
      ) : fields.isError ? (
        <LoadError error={fields.error} />
      ) : fields.data.length === 0 ? (
        <Card>
          <EmptyState icon={<SlidersHorizontal className="size-6" />} title="No contact fields yet">
            Add one to start recording more about your customers.
          </EmptyState>
        </Card>
      ) : (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead className="border-b border-border bg-surface-2 text-xs text-muted-foreground">
              <tr>
                {["Label", "Key", "Kind", "Options", ""].map((h, i) => (
                  <th key={i} scope="col" className="px-4 py-2.5 font-medium">
                    {h || <span className="sr-only">Actions</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {fields.data.map((f) => (
                <tr key={f.id}>
                  <td className="px-4 py-3 font-medium">{f.label}</td>
                  <td className="px-4 py-3 font-mono text-xs">{f.key}</td>
                  <td className="px-4 py-3">{KIND_LABEL[f.kind] ?? f.kind}</td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {f.options?.length ? f.options.join(", ") : "—"}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1">
                      <Button size="sm" variant="outline" onClick={() => setEditing(f)}>
                        <Pencil className="size-3.5" /> Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={del.isPending}
                        onClick={() =>
                          ask({
                            title: "Delete this field?",
                            message: "Every customer's value of it is removed.",
                            detail: f.label,
                            confirmLabel: "Delete",
                            run: () =>
                              del.mutateAsync(f.id).then(
                                () => void toast.success("Field deleted"),
                                // A segment uses it: the message says which.
                                (e) =>
                                  void toast.error(e instanceof ApiError ? e.message : "Failed"),
                              ),
                          })
                        }
                      >
                        <Trash2 className="size-3.5" /> Delete
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "Add contact field" : "Edit contact field"}
      >
        {editing !== null && (
          <FieldForm
            key={editing === "new" ? "new" : editing.id}
            field={editing === "new" ? null : editing}
            count={fields.data?.length ?? 0}
            onClose={() => setEditing(null)}
          />
        )}
      </Dialog>
    </div>
  );
}

function FieldForm({
  field,
  count,
  onClose,
}: {
  field: ContactField | null;
  count: number;
  onClose: () => void;
}) {
  const save = useSaveContactField();
  const [keyTouched, setKeyTouched] = useState(false);
  const [srv, setSrv] = useState<Record<string, string>>({});
  const form = useFormik({
    initialValues: {
      label: field?.label ?? "",
      key: field?.key ?? "",
      kind: (field?.kind ?? "text") as FieldKind,
      options: (field?.options ?? []).join("\n"),
      position: String(field?.position ?? count),
    },
    validationSchema: yup.object({
      label: yup.string().trim().required("Enter a label"),
      key: field
        ? yup.string()
        : yup.string().matches(KEY_RE, "Use a-z, 0-9 and _, starting with a letter"),
      options: yup
        .string()
        .test("options", "Add at least one option", function (v) {
          const n = splitOptions(v).length;
          if (this.parent.kind === "select" && n === 0) return false;
          return true;
        })
        .test("max", "At most 50 options", (v) => splitOptions(v).length <= 50),
      position: yup
        .number()
        .typeError("Enter 0 or more")
        .integer("Enter 0 or more")
        .min(0, "Enter 0 or more")
        .required("Enter 0 or more"),
    }),
    onSubmit: (v) => {
      setSrv({});
      save.mutate(
        {
          id: field?.id,
          label: v.label.trim(),
          ...(field ? {} : { key: v.key, kind: v.kind }),
          ...(v.kind === "select" ? { options: splitOptions(v.options) } : {}),
          position: Number(v.position),
        },
        {
          onSuccess: () => {
            toast.success("Field saved");
            onClose();
          },
          onError: (err) =>
            setSrv(
              err instanceof ApiError && err.status !== 422
                ? { _form: err.userMessage }
                : fieldErrors(err),
            ),
        },
      );
    },
  });
  const kind = form.values.kind;
  const errs = (k: string) => fieldError(form, k) ?? srv[k];

  return (
    <form noValidate className="space-y-4" onSubmit={form.handleSubmit}>
      <Field label="Label" htmlFor="cf-label" error={errs("label")}>
        <Input
          id="cf-label"
          maxLength={60}
          {...bind(form, "label")}
          onChange={(e) => {
            form.handleChange(e);
            if (!field && !keyTouched) form.setFieldValue("key", keyFromLabel(e.target.value));
          }}
        />
      </Field>
      <Field
        label="Key"
        htmlFor="cf-key"
        error={errs("key")}
        hint={field ? "The key and kind can't change once a field is made." : undefined}
      >
        <Input
          id="cf-key"
          className="font-mono"
          maxLength={40}
          {...bind(form, "key")}
          readOnly={!!field}
          disabled={!!field}
          autoCapitalize="none"
          spellCheck={false}
          onChange={(e) => {
            setKeyTouched(true);
            form.handleChange(e);
          }}
        />
      </Field>
      <Field label="Kind" htmlFor="cf-kind">
        <Select
          id="cf-kind"
          {...bindSelect(form, "kind")}
          options={KINDS}
          searchable={false}
          disabled={!!field}
        />
      </Field>
      {kind === "select" && (
        <Field
          label="Options"
          htmlFor="cf-options"
          error={errs("options")}
          hint="One per line, up to 50."
        >
          <Textarea id="cf-options" rows={5} {...bind(form, "options")} />
        </Field>
      )}
      <Field
        label="Position"
        htmlFor="cf-position"
        error={errs("position")}
        hint="0 is shown first."
      >
        <Input id="cf-position" type="number" min={0} {...bind(form, "position")} />
      </Field>
      <FormError message={srv._form} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          Save
        </Button>
      </div>
    </form>
  );
}

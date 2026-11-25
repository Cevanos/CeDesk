"use client";

import { Mail, Pencil, Plus, Send, Trash2 } from "lucide-react";
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
  Skeleton,
  Textarea,
} from "@/components/ui/primitives";
import { EmailChecks } from "@/components/workspace/email-checks";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import {
  useDeleteEmailAddress,
  useEmailAddresses,
  useSaveEmailAddress,
  useSendTestEmail,
} from "@/hooks/use-workspace";
import type { EmailAddress } from "@/lib/api/types";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";

export function EmailView() {
  const addresses = useEmailAddresses();
  const del = useDeleteEmailAddress();
  const test = useSendTestEmail();
  const { ask, dialog } = useConfirm();
  const [editing, setEditing] = useState<EmailAddress | "new" | null>(null);

  return (
    <div>
      {dialog}
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Support addresses your customers write to and get replies from. Emails sent to them become
          tickets by themselves, and the customer&apos;s answers are added to the same ticket.
        </p>
        <Button onClick={() => setEditing("new")}>
          <Plus className="size-4" /> Add address
        </Button>
      </div>

      {addresses.isPending ? (
        <Skeleton className="h-32" />
      ) : addresses.isError ? (
        <LoadError error={addresses.error} />
      ) : addresses.data.length === 0 ? (
        <Card>
          <EmptyState icon={<Mail className="size-6" />} title="No support addresses yet">
            Add the address customers email, like support@yourshop.com.
          </EmptyState>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-border">
            {addresses.data.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1 basis-56">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{a.address}</span>
                    {a.is_default && <Badge tone="primary">Default</Badge>}
                  </div>
                  <div className="text-xs text-muted-foreground">Shown as “{a.name}”</div>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={test.isPending && test.variables === a.id}
                  loading={test.isPending && test.variables === a.id}
                  onClick={() =>
                    test.mutate(a.id, {
                      onSuccess: (r) =>
                        toast.success(`Test email queued${r?.sent_to ? ` to ${r.sent_to}` : ""}`),
                    })
                  }
                >
                  <Send className="size-3.5" /> Send test
                </Button>
                <Button size="sm" variant="outline" onClick={() => setEditing(a)}>
                  <Pencil className="size-3.5" /> Edit
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={del.isPending}
                  onClick={() =>
                    ask({
                      title: "Remove this address?",
                      message: "Customers will no longer be able to email it to reach support.",
                      detail: a.address,
                      confirmLabel: "Remove",
                      run: () =>
                        del.mutate(a.id, { onSuccess: () => toast.success("Address removed") }),
                    })
                  }
                >
                  <Trash2 className="size-3.5" /> Delete
                </Button>
                <EmailChecks address={a} />
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "Add support address" : "Edit support address"}
      >
        {editing !== null && (
          <AddressForm
            key={editing === "new" ? "new" : editing.id}
            address={editing === "new" ? null : editing}
            onClose={() => setEditing(null)}
          />
        )}
      </Dialog>
    </div>
  );
}

function AddressForm({ address, onClose }: { address: EmailAddress | null; onClose: () => void }) {
  const save = useSaveEmailAddress();
  const [srv, setSrv] = useState<Record<string, string>>({});
  const form = useFormik({
    initialValues: {
      address: address?.address ?? "",
      name: address?.name ?? "",
      signature: address?.signature ?? "",
      is_default: address?.is_default ?? false,
    },
    validationSchema: yup.object({
      address: address
        ? yup.string()
        : yup
            .string()
            .trim()
            .matches(/^\S+@\S+\.\S+$/, "Enter a valid email address")
            .required("Enter a valid email address"),
      name: yup.string().trim().required("Enter the name customers will see"),
    }),
    onSubmit: (v) => {
      setSrv({});
      save.mutate(
        {
          id: address?.id,
          // The address itself can't be changed after it is created.
          ...(address ? {} : { address: v.address.trim() }),
          name: v.name.trim(),
          signature: v.signature,
          is_default: v.is_default,
        },
        {
          onSuccess: () => {
            toast.success("Address saved");
            onClose();
          },
          onError: (err) => setSrv(fieldErrors(err)),
        },
      );
    },
  });

  return (
    <form noValidate className="space-y-4" onSubmit={form.handleSubmit}>
      <Field
        label="Email address"
        htmlFor="ea-address"
        error={fieldError(form, "address") ?? srv.address}
        hint={address ? "The address can't be changed. Add a new one instead." : undefined}
      >
        <Input
          id="ea-address"
          type="email"
          inputMode="email"
          {...bind(form, "address")}
          disabled={!!address}
        />
      </Field>
      <Field label="Sender name" htmlFor="ea-name" error={fieldError(form, "name") ?? srv.name}>
        <Input id="ea-name" maxLength={80} {...bind(form, "name")} />
      </Field>
      <Field
        label="Signature"
        htmlFor="ea-sig"
        error={srv.signature}
        hint="Added to the end of replies sent from this address."
      >
        <Textarea id="ea-sig" rows={4} maxLength={2000} {...bind(form, "signature")} />
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4 accent-[var(--primary)]"
          name="is_default"
          checked={form.values.is_default}
          onChange={form.handleChange}
        />
        Use as the default sender
      </label>
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

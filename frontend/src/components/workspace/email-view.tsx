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
  const [addr, setAddr] = useState(address?.address ?? "");
  const [name, setName] = useState(address?.name ?? "");
  const [signature, setSignature] = useState(address?.signature ?? "");
  const [isDefault, setIsDefault] = useState(address?.is_default ?? false);
  const [errs, setErrs] = useState<Record<string, string>>({});

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const next: Record<string, string> = {};
        if (!address && !/^\S+@\S+\.\S+$/.test(addr.trim()))
          next.address = "Enter a valid email address";
        if (!name.trim()) next.name = "Enter the name customers will see";
        setErrs(next);
        if (Object.keys(next).length) return;
        save.mutate(
          {
            id: address?.id,
            // The address itself can't be changed after it is created.
            ...(address ? {} : { address: addr.trim() }),
            name: name.trim(),
            signature,
            is_default: isDefault,
          },
          {
            onSuccess: () => {
              toast.success("Address saved");
              onClose();
            },
            onError: (err) => setErrs(fieldErrors(err)),
          },
        );
      }}
    >
      <Field
        label="Email address"
        htmlFor="ea-address"
        error={errs.address}
        hint={address ? "The address can't be changed. Add a new one instead." : undefined}
      >
        <Input
          id="ea-address"
          type="email"
          inputMode="email"
          value={addr}
          disabled={!!address}
          onChange={(e) => setAddr(e.target.value)}
        />
      </Field>
      <Field label="Sender name" htmlFor="ea-name" error={errs.name}>
        <Input id="ea-name" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field
        label="Signature"
        htmlFor="ea-sig"
        error={errs.signature}
        hint="Added to the end of replies sent from this address."
      >
        <Textarea
          id="ea-sig"
          rows={4}
          maxLength={2000}
          value={signature}
          onChange={(e) => setSignature(e.target.value)}
        />
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4 accent-[var(--primary)]"
          checked={isDefault}
          onChange={(e) => setIsDefault(e.target.checked)}
        />
        Use as the default sender
      </label>
      <FormError message={errs._form} />
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

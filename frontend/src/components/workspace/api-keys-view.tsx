"use client";

import { KeyRound, Plus, Trash2 } from "lucide-react";
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
} from "@/components/ui/primitives";
import { SecretDialog } from "@/components/workspace/api-keys-secret-dialog";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from "@/hooks/use-workspace";
import type { ApiKeyAccess } from "@/hooks/workspace/api-keys";
import { ApiError } from "@/lib/api/client";
import { bind, bindSelect, fieldError, useFormik, yup } from "@/lib/forms";

const keySchema = yup.object({
  name: yup
    .string()
    .trim()
    .required("Name the key so you can tell it apart later")
    .max(80, "Use at most 80 characters"),
  access: yup.string().required("Choose what this key can do"),
});

const ACCESS: { value: ApiKeyAccess; label: string; help: string }[] = [
  { value: "read", label: "Read only", help: "Can only read (GET)." },
  {
    value: "read_write",
    label: "Read and write",
    help: "Also tickets, customers, notes, tags, tasks, time and customer messages.",
  },
  {
    value: "admin",
    label: "Admin",
    help: "Also teams, ticket types, saved replies, macros, rules and the help center.",
  },
];
const accessLabel = (a?: string) => ACCESS.find((x) => x.value === a)?.label ?? a ?? null;

function when(iso?: string | null) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString();
}

export function ApiKeysView() {
  const keys = useApiKeys();
  const revoke = useRevokeApiKey();
  const { ask, dialog: confirmDialog } = useConfirm();
  const [creating, setCreating] = useState(false);
  const [shown, setShown] = useState<string | null>(null);

  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          API keys let your own systems call CeDesk. A key is shown once, when you make it.
        </p>
        <Button onClick={() => setCreating(true)}>
          <Plus className="size-4" /> Make API key
        </Button>
      </div>

      {keys.isPending ? (
        <Skeleton className="h-32" />
      ) : keys.isError ? (
        <LoadError error={keys.error} />
      ) : keys.data.length === 0 ? (
        <Card>
          <EmptyState icon={<KeyRound className="size-6" />} title="No API keys yet">
            Make a key to connect your own tools.
          </EmptyState>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-border">
            {keys.data.map((k) => {
              const hint = k.hint ?? (k.prefix ? `${k.prefix}…` : k.last4 ? `…${k.last4}` : null);
              const created = when(k.created_at);
              const used = when(k.last_used_at);
              const revokedOn = when(k.revoked_at);
              const isRevoked = !!k.revoked_at;
              const access = accessLabel(k.access);
              return (
                <li
                  key={k.id}
                  className={`flex flex-wrap items-center gap-3 px-4 py-3 ${isRevoked ? "bg-surface-2/50 opacity-60" : ""}`}
                >
                  <div className="min-w-0 flex-1 basis-60">
                    <div className="truncate text-sm font-semibold">
                      {k.name || "Untitled key"}
                      {access && (
                        <span className="ml-2 rounded-full bg-surface-2 px-2 py-0.5 text-xs font-medium text-muted-foreground">
                          {access}
                        </span>
                      )}
                      {isRevoked && (
                        <span className="ml-2 rounded-full bg-surface-2 px-2 py-0.5 text-xs font-medium text-danger">
                          Revoked
                        </span>
                      )}
                    </div>
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      {hint && <span className="font-mono">{hint}</span>}
                      {created && (
                        <span>
                          {hint ? " · " : ""}Made {created}
                        </span>
                      )}
                      {" · "}
                      {used ? `Last used ${used}` : "Never used"}
                      {isRevoked && (
                        <span>
                          {" · "}Revoked{revokedOn ? ` ${revokedOn}` : ""}
                          {k.revoked_by_name ? ` by ${k.revoked_by_name}` : ""}
                        </span>
                      )}
                    </div>
                  </div>
                  {!isRevoked && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={revoke.isPending}
                      onClick={() =>
                        ask({
                          title: "Revoke this key?",
                          message: `Revoke “${k.name || "this key"}”? Anything using it stops working at once.`,
                          confirmLabel: "Revoke",
                          run: () =>
                            revoke.mutate(k.id, {
                              onSuccess: () => toast.success("API key revoked"),
                              onError: (e) =>
                                toast.error(
                                  e instanceof ApiError ? e.userMessage : "Couldn't revoke",
                                ),
                            }),
                        })
                      }
                    >
                      <Trash2 className="size-3.5" /> Revoke
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        </Card>
      )}

      <Dialog open={creating} onClose={() => setCreating(false)} title="Make API key">
        {creating && (
          <KeyForm
            onClose={() => setCreating(false)}
            onCreated={(secret) => {
              setCreating(false);
              setShown(secret);
            }}
          />
        )}
      </Dialog>
      <SecretDialog
        title="Your new API key"
        noun="API key"
        secret={shown}
        onClose={() => setShown(null)}
      />
      {confirmDialog}
    </div>
  );
}

function KeyForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (secret: string) => void;
}) {
  const create = useCreateApiKey();
  const [srv, setSrv] = useState<Record<string, string>>({});
  const form = useFormik({
    initialValues: { name: "", access: "" as ApiKeyAccess | "" },
    validationSchema: keySchema,
    onSubmit: (v) => {
      setSrv({});
      create.mutate(
        { name: v.name.trim(), access: v.access as ApiKeyAccess },
        {
          onSuccess: (r) => {
            const secret = r.key ?? r.token ?? r.secret;
            if (secret) onCreated(secret);
            else {
              toast.success("API key made");
              onClose();
            }
          },
          onError: (err) => setSrv(fieldErrors(err)),
        },
      );
    },
  });
  const access = form.values.access;

  return (
    <form noValidate className="space-y-4" onSubmit={form.handleSubmit}>
      <Field
        label="Name"
        htmlFor="ak-name"
        error={fieldError(form, "name") ?? srv.name}
        hint="For example “Billing sync”."
      >
        <Input id="ak-name" {...bind(form, "name")} maxLength={80} />
      </Field>
      <Field
        label="Access level"
        htmlFor="ak-access"
        error={fieldError(form, "access") ?? srv.access}
        hint={
          ACCESS.find((x) => x.value === access)?.help ??
          "At every level the key acts as you, and never reaches sign-in, members, security, SSO, imports or API keys."
        }
      >
        <Select
          id="ak-access"
          {...bindSelect(form, "access")}
          options={[
            { value: "", label: "Choose…" },
            ...ACCESS.map((x) => ({ value: x.value, label: x.label })),
          ]}
        />
      </Field>
      <p className="text-xs text-muted-foreground">
        The key is shown once, right after you make it. Copy it then.
      </p>
      <FormError message={srv._form} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={create.isPending}>
          Make key
        </Button>
      </div>
    </form>
  );
}

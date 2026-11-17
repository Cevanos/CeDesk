"use client";

import { KeyRound, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

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
                      onClick={() => {
                        if (
                          confirm(
                            `Revoke “${k.name || "this key"}”? Anything using it stops working at once.`,
                          )
                        )
                          revoke.mutate(k.id, {
                            onSuccess: () => toast.success("API key revoked"),
                            onError: (e) =>
                              toast.error(
                                e instanceof ApiError ? e.userMessage : "Couldn't revoke",
                              ),
                          });
                      }}
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
  const [name, setName] = useState("");
  const [access, setAccess] = useState<ApiKeyAccess | "">("");
  const [errs, setErrs] = useState<Record<string, string>>({});

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (!name.trim()) return setErrs({ name: "Name the key so you can tell it apart later" });
        if (!access) return setErrs({ access: "Choose what this key can do" });
        setErrs({});
        create.mutate(
          { name: name.trim(), access },
          {
            onSuccess: (r) => {
              const secret = r.key ?? r.token ?? r.secret;
              if (secret) onCreated(secret);
              else {
                toast.success("API key made");
                onClose();
              }
            },
            onError: (err) => setErrs(fieldErrors(err)),
          },
        );
      }}
    >
      <Field label="Name" htmlFor="ak-name" error={errs.name} hint="For example “Billing sync”.">
        <Input id="ak-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
      </Field>
      <Field
        label="Access level"
        htmlFor="ak-access"
        error={errs.access}
        hint={
          ACCESS.find((x) => x.value === access)?.help ??
          "At every level the key acts as you, and never reaches sign-in, members, security, SSO, imports or API keys."
        }
      >
        <Select
          id="ak-access"
          value={access}
          onChange={(e) => setAccess(e.target.value as ApiKeyAccess | "")}
          options={[
            { value: "", label: "Choose…" },
            ...ACCESS.map((x) => ({ value: x.value, label: x.label })),
          ]}
        />
      </Field>
      <p className="text-xs text-muted-foreground">
        The key is shown once, right after you make it. Copy it then.
      </p>
      <FormError message={errs._form} />
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

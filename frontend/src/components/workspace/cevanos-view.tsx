"use client";

import { CheckCircle2, Copy, PlugZap, Unplug, XCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { PasswordInput } from "@/components/auth/password-input";
import { useConfirm } from "@/components/ui/confirm";
import { copyText } from "@/components/ui/copy-button";
import { Badge, Button, Card, Field, Select, Skeleton } from "@/components/ui/primitives";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import {
  useCevanosConnection,
  useCheckCevanos,
  useDisconnectCevanos,
  useSaveCevanos,
  useTeams,
} from "@/hooks/use-workspace";
import type { CevanosConnection } from "@/lib/api/types";
import { bind, bindSelect, fieldError, useFormik, yup } from "@/lib/forms";

export function CevanosView() {
  const conn = useCevanosConnection();
  if (conn.isPending) return <Skeleton className="h-48" />;
  if (conn.isError) return <LoadError error={conn.error} />;
  return <CevanosBody key={`${conn.data.connected}-${conn.data.updated_at}`} conn={conn.data} />;
}

/**
 * The address Cevanos should call: the backend's public address plus the path
 * the API gives. The browser never learns the backend address on its own (the
 * proxy hides it), so set NEXT_PUBLIC_API_PUBLIC_URL to show a full address;
 * otherwise the Admin sees the path to append to it.
 */
/** What CeDesk handles today; shown when the server does not list the events itself. */
const DEFAULT_WEBHOOK_EVENTS = [
  "conversation.escalated",
  "conversation.classified",
  "conversation.reopened",
  "conversation.closed",
];

function webhookUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  const api = (process.env.NEXT_PUBLIC_API_PUBLIC_URL ?? "").trim().replace(/\/+$/, "");
  return `${api || "<API address>"}${path.startsWith("/") ? "" : "/"}${path}`;
}

function CevanosBody({ conn }: { conn: CevanosConnection }) {
  const save = useSaveCevanos();
  const disconnect = useDisconnectCevanos();
  const check = useCheckCevanos();
  const teams = useTeams();
  const { ask, dialog } = useConfirm();

  // Secrets are never sent back: the fields start empty and are only sent when typed.
  const [srv, setSrv] = useState<Record<string, string>>({});
  const form = useFormik({
    initialValues: {
      api_key: "",
      webhook_secret: "",
      close_on_resolve: conn.close_on_resolve,
      team_id: conn.team_id ?? "",
    },
    validationSchema: yup.object({
      api_key: yup
        .string()
        .trim()
        .max(500, "That key is too long")
        .matches(/^\S*$/, "The key has no spaces"),
      webhook_secret: yup
        .string()
        .trim()
        .max(500, "That secret is too long")
        .matches(/^\S*$/, "The secret has no spaces"),
    }),
    onSubmit: (v, h) => {
      setSrv({});
      save.mutate(
        {
          ...(v.api_key.trim() ? { api_key: v.api_key.trim() } : {}),
          ...(v.webhook_secret.trim() ? { webhook_secret: v.webhook_secret.trim() } : {}),
          close_on_resolve: v.close_on_resolve,
          team_id: v.team_id || null,
        },
        {
          onSuccess: () => {
            h.resetForm({
              values: { ...v, api_key: "", webhook_secret: "" },
            });
            toast.success("Cevanos settings saved");
          },
          onError: (err) => setSrv(fieldErrors(err)),
        },
      );
    },
  });
  const errs = (k: string) => fieldError(form, k) ?? srv[k];
  const url = webhookUrl(conn.webhook_path);

  return (
    <div className="space-y-5">
      {dialog}
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm text-muted-foreground">
          Connect Cevanos so AI chats that need a person arrive here as tickets and agents can reply
          to the customer.
        </p>
        <Badge tone={conn.connected ? "success" : "neutral"} className="ml-auto">
          {conn.connected ? "Connected" : "Not connected"}
        </Badge>
      </div>

      <Card className="p-4 sm:p-5">
        <form onSubmit={form.handleSubmit} noValidate className="space-y-4">
          <Field
            label="API key"
            htmlFor="cv-key"
            error={errs("api_key")}
            hint={
              conn.api_key_hint
                ? `Saved key ends in ${conn.api_key_hint}. Leave empty to keep it.`
                : "Paste the API key from Cevanos."
            }
          >
            <PasswordInput id="cv-key" autoComplete="off" {...bind(form, "api_key")} />
          </Field>
          <Field
            label="Webhook secret"
            htmlFor="cv-secret"
            error={errs("webhook_secret")}
            hint="Used to check that events really come from Cevanos. Leave empty to keep the saved one."
          >
            <PasswordInput id="cv-secret" autoComplete="off" {...bind(form, "webhook_secret")} />
          </Field>
          <Field
            label="Default team for chat tickets"
            htmlFor="cv-team"
            error={errs("team_id")}
            hint="Chats are routed to this team."
          >
            <Select
              id="cv-team"
              placeholder="No team"
              {...bindSelect(form, "team_id")}
              options={[
                { value: "", label: "No team" },
                ...(teams.data ?? []).map((t) => ({ value: t.id, label: t.name })),
              ]}
            />
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-[var(--primary)]"
              name="close_on_resolve"
              checked={form.values.close_on_resolve}
              onChange={form.handleChange}
            />
            Close the chat in Cevanos when the ticket is resolved
          </label>
          <FormError message={srv._form} />
          <div className="flex flex-wrap justify-end gap-2">
            {conn.connected && (
              <Button
                variant="ghost"
                loading={disconnect.isPending}
                onClick={() =>
                  ask({
                    title: "Disconnect Cevanos?",
                    message: "Chats will stop arriving as tickets.",
                    confirmLabel: "Disconnect",
                    run: () =>
                      disconnect.mutate(undefined, {
                        onSuccess: () => toast.success("Cevanos disconnected"),
                      }),
                  })
                }
              >
                <Unplug className="size-4" /> Disconnect
              </Button>
            )}
            <Button type="submit" loading={save.isPending}>
              {conn.connected ? "Save changes" : "Connect"}
            </Button>
          </div>
        </form>
      </Card>

      <Card className="space-y-3 p-4 sm:p-5">
        <div>
          <h2 className="text-base font-semibold">Webhook address</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            In Cevanos, add a webhook at this address and use the signing secret you saved above.
            Without it, no handover from the AI agent reaches CeDesk.
          </p>
        </div>
        {url.startsWith("<API") && (
          <p className="text-xs text-warning">
            Replace <code>&lt;API address&gt;</code> with your API address, or set
            NEXT_PUBLIC_API_PUBLIC_URL so it shows in full.
          </p>
        )}
        <div className="flex items-center gap-2">
          <code
            className="min-w-0 flex-1 overflow-x-auto rounded-lg bg-surface-2 px-3 py-2 text-xs"
            aria-label="Webhook address"
          >
            {url}
          </code>
          <Button
            variant="outline"
            size="icon"
            aria-label="Copy webhook address"
            onClick={async () => {
              // copyText also works on plain-http pages, where navigator.clipboard is missing.
              if (await copyText(url)) toast.success("Copied");
              else toast.error("Couldn't copy. Select the address and copy it.");
            }}
          >
            <Copy className="size-4" />
          </Button>
        </div>
      </Card>

      <Card className="space-y-3 p-4 sm:p-5">
        <div>
          <h2 className="text-base font-semibold">Webhook events</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Subscribe the Cevanos webhook to these events.
          </p>
        </div>
        <ul aria-label="Webhook events" className="flex flex-wrap gap-2">
          {(conn.webhook_events?.length ? conn.webhook_events : DEFAULT_WEBHOOK_EVENTS).map((e) => (
            <li key={e}>
              <code className="rounded-lg bg-surface-2 px-2.5 py-1 text-xs">{e}</code>
            </li>
          ))}
        </ul>
      </Card>

      <Card className="space-y-3 p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold">Test the key</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Checks that the saved key works and has the access it needs.
            </p>
          </div>
          <Button
            variant="outline"
            loading={check.isPending}
            disabled={!conn.connected}
            onClick={() => check.mutate()}
          >
            <PlugZap className="size-4" /> Test the key
          </Button>
        </div>
        {check.data && (
          <div
            role="status"
            className={
              check.data.ok
                ? "flex gap-2 rounded-lg bg-success-soft px-3 py-2 text-sm text-success"
                : "flex gap-2 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger"
            }
          >
            {check.data.ok ? (
              <CheckCircle2 className="mt-0.5 size-4 flex-none" aria-hidden />
            ) : (
              <XCircle className="mt-0.5 size-4 flex-none" aria-hidden />
            )}
            <div>
              {check.data.message}
              {check.data.missing_scopes.length > 0 && (
                <div className="mt-1 text-xs">
                  Missing access: {check.data.missing_scopes.join(", ")}
                </div>
              )}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

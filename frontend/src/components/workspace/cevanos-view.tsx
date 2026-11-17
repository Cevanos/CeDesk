"use client";

import { CheckCircle2, Copy, PlugZap, Unplug, XCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { PasswordInput } from "@/components/auth/password-input";
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
/** What CDesk handles today; shown when the server does not list the events itself. */
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

  // Secrets are never sent back: the fields start empty and are only sent when typed.
  const [apiKey, setApiKey] = useState("");
  const [secret, setSecret] = useState("");
  const [closeOnResolve, setCloseOnResolve] = useState(conn.close_on_resolve);
  const [teamId, setTeamId] = useState(conn.team_id ?? "");
  const [errs, setErrs] = useState<Record<string, string>>({});
  const url = webhookUrl(conn.webhook_path);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    save.mutate(
      {
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        ...(secret.trim() ? { webhook_secret: secret.trim() } : {}),
        close_on_resolve: closeOnResolve,
        team_id: teamId || null,
      },
      {
        onSuccess: () => {
          setApiKey("");
          setSecret("");
          setErrs({});
          toast.success("Cevanos settings saved");
        },
        onError: (err) => setErrs(fieldErrors(err)),
      },
    );
  };

  return (
    <div className="space-y-5">
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
        <form onSubmit={submit} noValidate className="space-y-4">
          <Field
            label="API key"
            htmlFor="cv-key"
            error={errs.api_key}
            hint={
              conn.api_key_hint
                ? `Saved key ends in ${conn.api_key_hint}. Leave empty to keep it.`
                : "Paste the API key from Cevanos."
            }
          >
            <PasswordInput
              id="cv-key"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </Field>
          <Field
            label="Webhook secret"
            htmlFor="cv-secret"
            error={errs.webhook_secret}
            hint="Used to check that events really come from Cevanos. Leave empty to keep the saved one."
          >
            <PasswordInput
              id="cv-secret"
              autoComplete="off"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
            />
          </Field>
          <Field
            label="Default team for chat tickets"
            htmlFor="cv-team"
            error={errs.team_id}
            hint="Chats are routed to this team."
          >
            <Select
              id="cv-team"
              placeholder="No team"
              value={teamId}
              options={[
                { value: "", label: "No team" },
                ...(teams.data ?? []).map((t) => ({ value: t.id, label: t.name })),
              ]}
              onChange={(e) => setTeamId(e.target.value)}
            />
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-[var(--primary)]"
              checked={closeOnResolve}
              onChange={(e) => setCloseOnResolve(e.target.checked)}
            />
            Close the chat in Cevanos when the ticket is resolved
          </label>
          <FormError message={errs._form} />
          <div className="flex flex-wrap justify-end gap-2">
            {conn.connected && (
              <Button
                variant="ghost"
                loading={disconnect.isPending}
                onClick={() => {
                  if (confirm("Disconnect Cevanos? Chats will stop arriving as tickets."))
                    disconnect.mutate(undefined, {
                      onSuccess: () => toast.success("Cevanos disconnected"),
                    });
                }}
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
            Without it, no handover from the AI agent reaches CDesk.
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
            onClick={() =>
              navigator.clipboard
                ?.writeText(url)
                .then(() => toast.success("Copied"))
                .catch(() => toast.error("Couldn't copy. Select the address and copy it."))
            }
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

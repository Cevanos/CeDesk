"use client";

import { KeyRound, Pencil, Plus, RefreshCw, Send, Trash2, Webhook } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Dialog } from "@/components/ui/overlay";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Skeleton,
} from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { SecretDialog } from "@/components/workspace/api-keys-secret-dialog";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import {
  useDeleteWebhook,
  useRetryDelivery,
  useRotateWebhookSecret,
  useSaveWebhook,
  useTestWebhook,
  useWebhookDeliveries,
  useWebhooks,
} from "@/hooks/use-workspace";
import type { WebhookDelivery, WebhookRow } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";

const EVENT_HINTS = [
  "ticket.created",
  "ticket.updated",
  "ticket.resolved",
  "message.created",
  "assignment.changed",
];

const isOn = (w: WebhookRow) => w.active ?? w.enabled ?? true;
const errMsg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.userMessage : fallback);

function stamp(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}

export function WebhooksView() {
  const hooks = useWebhooks();
  const save = useSaveWebhook();
  const del = useDeleteWebhook();
  const rotate = useRotateWebhookSecret();
  const test = useTestWebhook();
  const [editing, setEditing] = useState<WebhookRow | "new" | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);

  return (
    <div className="space-y-8">
      <div>
        <div className="mb-4 flex items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            We send each event you pick to your URL, signed with the webhook&apos;s secret. The
            secret is shown once.
          </p>
          <Button onClick={() => setEditing("new")}>
            <Plus className="size-4" /> Add webhook
          </Button>
        </div>

        {hooks.isPending ? (
          <Skeleton className="h-32" />
        ) : hooks.isError ? (
          <LoadError error={hooks.error} />
        ) : hooks.data.length === 0 ? (
          <Card>
            <EmptyState icon={<Webhook className="size-6" />} title="No webhooks yet">
              Add a URL to be told when tickets and messages change.
            </EmptyState>
          </Card>
        ) : (
          <Card className="overflow-hidden">
            <ul className="divide-y divide-border">
              {hooks.data.map((w) => (
                <li key={w.id} className="flex flex-wrap items-start gap-3 px-4 py-3">
                  <div className="min-w-0 flex-1 basis-72">
                    <div className="truncate font-mono text-sm font-semibold">{w.url}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {(w.events ?? []).map((ev) => (
                        <Badge key={ev}>{ev}</Badge>
                      ))}
                      {!isOn(w) && <Badge tone="warning">Paused</Badge>}
                    </div>
                    {w.secret_hint && (
                      <div className="mt-1 text-xs text-muted-foreground">
                        Secret ends {w.secret_hint}
                      </div>
                    )}
                  </div>
                  <Switch
                    label={`${w.url} active`}
                    checked={isOn(w)}
                    disabled={save.isPending}
                    onChange={(v) =>
                      save.mutate(
                        { id: w.id, active: v },
                        { onError: (e) => toast.error(errMsg(e, "Couldn't change the webhook")) },
                      )
                    }
                  />
                  <div className="flex flex-wrap gap-1.5">
                    <Button
                      size="sm"
                      variant="outline"
                      loading={test.isPending && test.variables === w.id}
                      onClick={() =>
                        test.mutate(w.id, {
                          onSuccess: () => toast.success("Test ping sent"),
                          onError: (e) => toast.error(errMsg(e, "The test ping failed")),
                        })
                      }
                    >
                      <Send className="size-3.5" /> Test
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => setEditing(w)}>
                      <Pencil className="size-3.5" /> Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={rotate.isPending}
                      onClick={() => {
                        if (confirm("Make a new secret? The old one stops working at once."))
                          rotate.mutate(w.id, {
                            onSuccess: (r) =>
                              r.secret ? setSecret(r.secret) : toast.success("New secret made"),
                            onError: (e) => toast.error(errMsg(e, "Couldn't make a new secret")),
                          });
                      }}
                    >
                      <KeyRound className="size-3.5" /> New secret
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={del.isPending}
                      onClick={() => {
                        if (confirm("Delete this webhook? Its queue and log are deleted too."))
                          del.mutate(w.id, {
                            onSuccess: () => toast.success("Webhook deleted"),
                            onError: (e) => toast.error(errMsg(e, "Couldn't delete")),
                          });
                      }}
                    >
                      <Trash2 className="size-3.5" /> Delete
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>

      <section>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">Delivery log</h2>
          {!showLog && (
            <Button size="sm" variant="outline" onClick={() => setShowLog(true)}>
              Show log
            </Button>
          )}
        </div>
        {showLog && <DeliveryLog />}
      </section>

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "Add webhook" : "Edit webhook"}
      >
        {editing !== null && (
          <WebhookForm
            key={editing === "new" ? "new" : editing.id}
            hook={editing === "new" ? null : editing}
            onClose={() => setEditing(null)}
            onCreated={(s) => {
              setEditing(null);
              setSecret(s);
            }}
          />
        )}
      </Dialog>
      <SecretDialog
        title="Your webhook secret"
        noun="secret"
        secret={secret}
        onClose={() => setSecret(null)}
      />
    </div>
  );
}

function DeliveryLog() {
  const log = useWebhookDeliveries();
  const retry = useRetryDelivery();

  if (log.isPending) return <Skeleton className="h-24" />;
  if (log.isError) return <LoadError error={log.error} />;
  if (log.data.length === 0)
    return (
      <Card>
        <p className="px-4 py-6 text-center text-sm text-muted-foreground">No deliveries yet.</p>
      </Card>
    );
  const ok = (d: WebhookDelivery) => ["delivered", "success", "ok"].includes(d.status ?? "");
  return (
    <Card className="overflow-hidden">
      <ul className="divide-y divide-border">
        {log.data.map((d) => (
          <li key={d.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-sm">
            <div className="min-w-0 flex-1 basis-56">
              <div className="font-mono text-[13px]">{d.event ?? "event"}</div>
              <div className="text-xs text-muted-foreground">
                {stamp(d.created_at)}
                {d.response_status ? ` · HTTP ${d.response_status}` : ""}
                {d.attempts ? ` · ${d.attempts} attempt${d.attempts === 1 ? "" : "s"}` : ""}
                {d.error ? ` · ${d.error}` : ""}
              </div>
            </div>
            <Badge tone={ok(d) ? "success" : d.status === "pending" ? "neutral" : "danger"}>
              {d.status ?? "unknown"}
            </Badge>
            {!ok(d) && (
              <Button
                size="sm"
                variant="outline"
                loading={retry.isPending && retry.variables === d.id}
                onClick={() =>
                  retry.mutate(d.id, {
                    onSuccess: () => toast.success("Sent again"),
                    onError: (e) => toast.error(errMsg(e, "Couldn't retry")),
                  })
                }
              >
                <RefreshCw className="size-3.5" /> Retry
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function WebhookForm({
  hook,
  onClose,
  onCreated,
}: {
  hook: WebhookRow | null;
  onClose: () => void;
  onCreated: (secret: string) => void;
}) {
  const save = useSaveWebhook();
  const [url, setUrl] = useState(hook?.url ?? "");
  const [events, setEvents] = useState((hook?.events ?? ["ticket.created"]).join(", "));
  const [errs, setErrs] = useState<Record<string, string>>({});

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const list = events
          .split(/[\s,]+/)
          .map((s) => s.trim())
          .filter(Boolean);
        const next: Record<string, string> = {};
        if (!/^https?:\/\/\S+$/i.test(url.trim()))
          next.url = "Enter a full URL starting with https://";
        if (list.length === 0) next.events = "Pick at least one event";
        setErrs(next);
        if (Object.keys(next).length) return;
        save.mutate(
          { id: hook?.id, url: url.trim(), events: list },
          {
            onSuccess: (r) => {
              if (!hook && r.secret) return onCreated(r.secret);
              toast.success("Webhook saved");
              onClose();
            },
            onError: (err) => setErrs(fieldErrors(err)),
          },
        );
      }}
    >
      <Field label="URL" htmlFor="wh-url" error={errs.url}>
        <Input
          id="wh-url"
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://example.com/hooks/cedesk"
          autoCapitalize="none"
          spellCheck={false}
        />
      </Field>
      <Field
        label="Events"
        htmlFor="wh-events"
        error={errs.events}
        hint={`Separate with commas, e.g. ${EVENT_HINTS.join(", ")}`}
      >
        <Input
          id="wh-events"
          value={events}
          onChange={(e) => setEvents(e.target.value)}
          autoCapitalize="none"
          spellCheck={false}
        />
      </Field>
      {!hook && (
        <p className="text-xs text-muted-foreground">
          A secret is made for this webhook. You&apos;ll see it once, right after you save.
        </p>
      )}
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

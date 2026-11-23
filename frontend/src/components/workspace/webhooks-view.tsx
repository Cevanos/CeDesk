"use client";

import { KeyRound, Pencil, Plus, RefreshCw, Send, Trash2, Webhook } from "lucide-react";
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
import type { WebhookRow } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";

const EVENTS: { value: string; label: string }[] = [
  { value: "ticket.created", label: "A ticket is created" },
  {
    value: "ticket.updated",
    label: "A ticket changes (status, priority, team, assignee, type)",
  },
  { value: "ticket.resolved", label: "A ticket is resolved" },
  { value: "message.created", label: "A customer or agent replies (never notes)" },
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
  const { ask, dialog: confirmDialog } = useConfirm();
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
                        { id: w.id, enabled: v },
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
                          onSuccess: (d) => {
                            if (d?.state === "failed") {
                              toast.error(
                                `Failed: ${d.last_error || (d.last_status ? `HTTP ${d.last_status}` : "no answer")}`,
                              );
                            } else if (d?.state === "delivered") {
                              toast.success(
                                `Delivered: HTTP ${d.last_status ?? "OK"}${d.last_ms != null ? ` in ${d.last_ms} ms` : ""}`,
                              );
                            } else {
                              toast.message("Test ping queued");
                            }
                          },
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
                      onClick={() =>
                        ask({
                          title: "Make a new secret?",
                          message: "Make a new secret? The old one stops working at once.",
                          confirmLabel: "New secret",
                          tone: "warning",
                          run: () =>
                            rotate.mutate(w.id, {
                              onSuccess: (r) =>
                                r.secret ? setSecret(r.secret) : toast.success("New secret made"),
                              onError: (e) => toast.error(errMsg(e, "Couldn't make a new secret")),
                            }),
                        })
                      }
                    >
                      <KeyRound className="size-3.5" /> New secret
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={del.isPending}
                      onClick={() =>
                        ask({
                          title: "Delete this webhook?",
                          message: "Delete this webhook? Its queue and log are deleted too.",
                          confirmLabel: "Delete",
                          run: () =>
                            del.mutate(w.id, {
                              onSuccess: () => toast.success("Webhook deleted"),
                              onError: (e) => toast.error(errMsg(e, "Couldn't delete")),
                            }),
                        })
                      }
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
      {confirmDialog}
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
  return (
    <Card className="overflow-hidden">
      <ul className="divide-y divide-border">
        {log.data.map((d) => {
          const tries =
            typeof d.tries === "number"
              ? d.tries
              : Array.isArray(d.attempts)
                ? d.attempts.length
                : 0;
          const attempts = Array.isArray(d.attempts) ? d.attempts : [];
          const failed = d.state === "failed";
          return (
            <li key={d.id} className="px-4 py-2.5 text-sm">
              <div className="flex flex-wrap items-center gap-3">
                <div className="min-w-0 flex-1 basis-56">
                  <div className="font-mono text-[13px]">{d.event ?? "event"}</div>
                  <div className="text-xs text-muted-foreground">
                    {stamp(d.created_at)}
                    {d.last_status ? ` · HTTP ${d.last_status}` : ""}
                    {tries ? ` · ${tries} attempt${tries === 1 ? "" : "s"}` : ""}
                    {failed &&
                    d.last_error &&
                    d.last_error.replace(/\.$/, "").trim() !== `HTTP ${d.last_status}`
                      ? ` · ${d.last_error}`
                      : ""}
                  </div>
                </div>
                {d.state === "delivered" && <Badge tone="success">Delivered</Badge>}
                {d.state === "pending" && (
                  <Badge tone="warning">
                    {d.next_try_at ? `Retrying at ${stamp(d.next_try_at)}` : "Retrying"}
                  </Badge>
                )}
                {failed && <Badge tone="danger">Failed</Badge>}
                {failed && (
                  <Button
                    size="sm"
                    variant="outline"
                    loading={retry.isPending && retry.variables === d.id}
                    onClick={() =>
                      retry.mutate(d.id, {
                        onSuccess: () => toast.success("Queued to send again"),
                        onError: (e) => toast.error(errMsg(e, "Couldn't retry")),
                      })
                    }
                  >
                    <RefreshCw className="size-3.5" /> Retry
                  </Button>
                )}
              </div>
              {attempts.length > 0 && (
                <details className="mt-1.5 text-xs text-muted-foreground">
                  <summary className="cursor-pointer select-none">Show attempts</summary>
                  <ol className="mt-1.5 space-y-1 pl-4">
                    {attempts.map((a, i) => (
                      <li key={i} className="list-decimal">
                        {stamp(a.at)}
                        {a.status ? ` · HTTP ${a.status}` : ""}
                        {a.ms != null ? ` · ${a.ms} ms` : ""}
                        {a.error ? ` · ${a.error}` : ""}
                        {a.response ? (
                          <span className="block truncate font-mono">{a.response}</span>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                </details>
              )}
            </li>
          );
        })}
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
  const [events, setEvents] = useState<string[]>(
    (hook?.events ?? ["ticket.created"]).filter((e) => EVENTS.some((x) => x.value === e)),
  );
  // New webhooks start off, so the Admin can Test before real ticket events flow.
  const [enabled, setEnabled] = useState(hook ? isOn(hook) : false);
  const [errs, setErrs] = useState<Record<string, string>>({});

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const list = events;
        const next: Record<string, string> = {};
        if (!/^https?:\/\/\S+$/i.test(url.trim()))
          next.url = "Enter a full URL starting with https://";
        if (list.length === 0) next.events = "Pick at least one event";
        setErrs(next);
        if (Object.keys(next).length) return;
        save.mutate(
          { id: hook?.id, url: url.trim(), events: list, enabled },
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
          onChange={(e) => {
            setUrl(e.target.value);
            setErrs((p) => ({ ...p, url: "" }));
          }}
          placeholder="https://example.com/hooks/cedesk"
          autoCapitalize="none"
          spellCheck={false}
        />
      </Field>
      <fieldset>
        <legend className="mb-1.5 text-sm font-medium">Events</legend>
        <div className="space-y-2">
          {EVENTS.map((ev) => (
            <label key={ev.value} className="flex items-start gap-2.5 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 size-4 accent-primary"
                checked={events.includes(ev.value)}
                onChange={(e) => {
                  setEvents((cur) =>
                    e.target.checked ? [...cur, ev.value] : cur.filter((x) => x !== ev.value),
                  );
                  setErrs((p) => ({ ...p, events: "" }));
                }}
              />
              <span>
                {ev.label}
                <span className="block font-mono text-xs text-muted-foreground">{ev.value}</span>
              </span>
            </label>
          ))}
        </div>
        {errs.events && (
          <p role="alert" className="mt-1.5 text-xs text-danger">
            {errs.events}
          </p>
        )}
      </fieldset>
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm">
          <div className="font-medium">Active</div>
          <div className="text-xs text-muted-foreground">
            {hook
              ? "Turn off to stop sending."
              : "Off by default, so you can Test it before real events are sent."}
          </div>
        </div>
        <Switch label="Active" checked={enabled} onChange={setEnabled} />
      </div>
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

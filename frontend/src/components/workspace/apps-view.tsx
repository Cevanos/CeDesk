"use client";

import { Plug } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { CopyButton } from "@/components/ui/copy-button";
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
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import { useCurrentUser } from "@/hooks/use-auth";
import { secondKey, useApps, useSaveApp, useUninstallApp } from "@/hooks/use-workspace";
import type { AppCredential, AppField, WorkspaceApp } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";

const labelOf = (c: AppCredential | string | null | undefined, fallback: string) =>
  !c ? fallback : typeof c === "string" ? humanize(c) : (c.label ?? humanize(c.key) ?? fallback);

function humanize(s?: string) {
  if (!s) return "";
  const t = s.replace(/_/g, " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export function AppsView() {
  const apps = useApps();
  const remove = useUninstallApp();
  const { ask, dialog } = useConfirm();
  const [editing, setEditing] = useState<WorkspaceApp | null>(null);

  if (apps.isPending) return <Skeleton className="h-40" />;
  if (apps.isError) return <LoadError error={apps.error} />;
  if (apps.data.length === 0)
    return (
      <Card>
        <EmptyState icon={<Plug className="size-6" />} title="No apps available">
          Apps you can connect to CeDesk show up here.
        </EmptyState>
      </Card>
    );

  return (
    <div>
      {dialog}
      <p className="mb-4 text-sm text-muted-foreground">
        Connect other tools. Keys and secrets are stored safely and never shown again.
      </p>
      <Card className="overflow-hidden">
        <ul className="divide-y divide-border">
          {apps.data.map((a) => (
            <li key={a.app} className="flex flex-wrap items-start gap-3 px-4 py-3">
              <div className="min-w-0 flex-1 basis-60">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold">{a.name ?? humanize(a.app)}</span>
                  {a.installed && <Badge tone="success">Installed</Badge>}
                  {a.blocked && <Badge tone="warning">Unavailable</Badge>}
                </div>
                {a.description && (
                  <p className="mt-0.5 text-sm text-muted-foreground">{a.description}</p>
                )}
                {a.blocked && <p className="mt-1 text-xs text-warning">{a.blocked}</p>}
                {a.installed && a.secret_hint && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Credential ends {a.secret_hint}
                  </p>
                )}
              </div>
              {a.installed ? (
                <>
                  <Button size="sm" variant="outline" onClick={() => setEditing(a)}>
                    Settings
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={remove.isPending}
                    onClick={() =>
                      ask({
                        title: `Uninstall ${a.name ?? humanize(a.app)}?`,
                        message:
                          a.app === "calendly"
                            ? "The saved reply /book is removed too (kept if you've edited it)."
                            : a.app === "jira"
                              ? "Its credential is deleted, so it has to be pasted again to reinstall. Issues already in Jira stay there."
                              : "Its credential is deleted, so it has to be pasted again to reinstall.",
                        confirmLabel: "Uninstall",
                        run: () =>
                          remove.mutate(a.app, {
                            onSuccess: () => toast.success("App uninstalled"),
                            onError: (e) =>
                              toast.error(
                                e instanceof ApiError ? e.userMessage : "Couldn't uninstall",
                              ),
                          }),
                      })
                    }
                  >
                    Uninstall
                  </Button>
                </>
              ) : (
                <Button size="sm" disabled={!!a.blocked} onClick={() => setEditing(a)}>
                  Install
                </Button>
              )}
            </li>
          ))}
        </ul>
      </Card>

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={
          editing
            ? `${editing.installed ? "Settings for" : "Install"} ${editing.name ?? humanize(editing.app)}`
            : ""
        }
      >
        {editing && <AppForm key={editing.app} app={editing} onClose={() => setEditing(null)} />}
      </Dialog>
    </div>
  );
}

/** The URL to paste into Slack's Event Subscriptions. */
function SlackEventsUrl() {
  const { user } = useCurrentUser();
  // The backend's public https address (same as the Cevanos page), not the web app's origin.
  const api = (process.env.NEXT_PUBLIC_API_PUBLIC_URL ?? "").trim().replace(/\/+$/, "");
  const url = `${api || "<API address>"}/api/v1/slack/events/${user?.workspace_id ?? "<workspace_id>"}`;
  return (
    <Field
      label="Events Request URL"
      htmlFor="app-events-url"
      hint="Paste this into your Slack app under Event Subscriptions."
    >
      <div className="flex gap-2">
        <Input id="app-events-url" readOnly value={url} onFocus={(e) => e.currentTarget.select()} />
        <CopyButton text={url} label="Copy URL" />
      </div>
    </Field>
  );
}

/** The server's setup steps for an app, with the real API address and workspace id filled in. */
function CredentialHelp({ app }: { app: WorkspaceApp }) {
  const { user } = useCurrentUser();
  if (!app.credential_help) return null;
  const api = (process.env.NEXT_PUBLIC_API_PUBLIC_URL ?? "").trim().replace(/\/+$/, "");
  let text = app.credential_help;
  if (api) text = text.replaceAll("<CeDesk API>", api);
  if (user?.workspace_id) text = text.replaceAll("<workspace id>", user.workspace_id);
  return (
    <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm wrap-break-word text-muted-foreground">
      {text}
    </p>
  );
}

/** The install form built from the server's `fields` list: label, hint, password boxes, all required. */
function AppFieldsForm({ app, onClose }: { app: WorkspaceApp; onClose: () => void }) {
  const save = useSaveApp();
  const fields = app.fields ?? [];
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      fields.map((f) => [
        f.key,
        f.send_as === "settings" ? String(app.settings?.[f.key] ?? "") : "",
      ]),
    ),
  );
  const [errs, setErrs] = useState<Record<string, string>>({});
  const needsEventsUrl = fields.some((f) => f.send_as === "signing_secret");

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const next: Record<string, string> = {};
        for (const f of fields) {
          // An installed app keeps a saved secret when its box is left blank.
          const keepsSaved = app.installed && f.send_as !== "settings";
          if (!keepsSaved && !values[f.key]?.trim()) next[f.key] = `Enter ${f.label}`;
        }
        setErrs(next);
        if (Object.keys(next).length) return;

        const settings: Record<string, string> = {};
        const extra: Record<string, string> = {};
        for (const f of fields) {
          const v = (values[f.key] ?? "").trim();
          if (f.send_as === "settings") settings[f.key] = v;
          else if (v) extra[f.send_as] = v;
        }
        const { secret, ...rest } = extra;
        save.mutate(
          { app: app.app, settings, ...(secret ? { secret } : {}), extra: rest },
          {
            onSuccess: (res) => {
              toast.success("App saved");
              if (res?.note) toast.warning(res.note, { duration: 12_000 });
              onClose();
            },
            onError: (err) =>
              setErrs(
                err instanceof ApiError && err.status !== 422
                  ? { _form: err.userMessage }
                  : fieldErrors(err),
              ),
          },
        );
      }}
    >
      <CredentialHelp app={app} />
      {fields.map((f) => (
        <Field
          key={f.key}
          label={f.label}
          htmlFor={`app-${f.key}`}
          error={
            errs[f.key] ??
            errs[`settings.${f.key}`] ??
            (f.send_as !== "settings" ? errs[f.send_as] : undefined)
          }
          hint={
            f.send_as !== "settings" && app.installed
              ? `${f.hint ? `${f.hint} ` : ""}Saved. Leave blank to keep it.`
              : (f.hint ?? undefined)
          }
        >
          <Input
            id={`app-${f.key}`}
            type={f.secret ? "password" : "text"}
            autoComplete="off"
            value={values[f.key] ?? ""}
            onChange={(e) => {
              setValues((v) => ({ ...v, [f.key]: e.target.value }));
              setErrs((p) => ({ ...p, [f.key]: "" }));
            }}
            autoCapitalize="none"
            spellCheck={false}
          />
        </Field>
      ))}
      {needsEventsUrl && <SlackEventsUrl />}
      {fields.length === 0 && (
        <p className="text-sm text-muted-foreground">This app needs no settings.</p>
      )}
      <FormError message={errs._form} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          {app.installed ? "Save" : "Install"}
        </Button>
      </div>
    </form>
  );
}

function AppForm({ app, onClose }: { app: WorkspaceApp; onClose: () => void }) {
  if (app.fields && app.fields.length > 0) return <AppFieldsForm app={app} onClose={onClose} />;
  return <LegacyAppForm app={app} onClose={onClose} />;
}

function LegacyAppForm({ app, onClose }: { app: WorkspaceApp; onClose: () => void }) {
  const save = useSaveApp();
  const fields: AppField[] = app.settings_fields ?? [];
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((f) => [f.key, String(app.settings?.[f.key] ?? "")])),
  );
  const [credential, setCredential] = useState("");
  const [second, setSecond] = useState("");
  const [errs, setErrs] = useState<Record<string, string>>({});

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const next: Record<string, string> = {};
        for (const f of fields)
          if (f.required && !values[f.key]?.trim()) next[f.key] = `Enter ${f.label ?? f.key}`;
        if (!app.installed && app.credential && !credential.trim())
          next.credential = `Enter ${labelOf(app.credential, "the credential")}`;
        setErrs(next);
        if (Object.keys(next).length) return;
        save.mutate(
          {
            app: app.app,
            settings: values,
            // Left blank on an installed app = keep the saved credential.
            ...(credential.trim() ? { secret: credential.trim() } : {}),
            ...(second.trim()
              ? { extra: { [secondKey(app.second_credential)]: second.trim() } }
              : {}),
          },
          {
            onSuccess: (res) => {
              toast.success("App saved");
              // A warning from the server, e.g. "/book was edited by hand".
              if (res?.note) toast.warning(res.note, { duration: 12_000 });
              onClose();
            },
            onError: (err) =>
              setErrs(
                err instanceof ApiError && err.status !== 422
                  ? { _form: err.userMessage }
                  : fieldErrors(err),
              ),
          },
        );
      }}
    >
      <CredentialHelp app={app} />
      {fields.map((f) => (
        <Field
          key={f.key}
          label={f.label ?? humanize(f.key)}
          htmlFor={`app-${f.key}`}
          error={errs[f.key] ?? errs[`settings.${f.key}`]}
          hint={f.hint}
        >
          <Input
            id={`app-${f.key}`}
            type={f.type === "number" ? "number" : f.type === "url" ? "url" : "text"}
            value={values[f.key] ?? ""}
            onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
            autoCapitalize="none"
            spellCheck={false}
          />
        </Field>
      ))}
      {app.credential && (
        <Field
          label={labelOf(app.credential, "Credential")}
          htmlFor="app-credential"
          error={errs.credential}
          hint={
            app.installed
              ? `Saved${app.secret_hint ? ` (ends ${app.secret_hint})` : ""}. Leave blank to keep it.`
              : "Stored safely; it isn't shown again."
          }
        >
          <Input
            id="app-credential"
            type="password"
            autoComplete="off"
            value={credential}
            onChange={(e) => setCredential(e.target.value)}
          />
        </Field>
      )}
      {app.second_credential && (
        <Field
          label={labelOf(app.second_credential, "Second credential")}
          htmlFor="app-second"
          error={errs.second_credential}
          hint={app.installed ? "Leave blank to keep the saved one." : undefined}
        >
          <Input
            id="app-second"
            type="password"
            autoComplete="off"
            value={second}
            onChange={(e) => setSecond(e.target.value)}
          />
        </Field>
      )}
      {app.second_credential && <SlackEventsUrl />}
      {fields.length === 0 && !app.credential && !app.second_credential && (
        <p className="text-sm text-muted-foreground">This app needs no settings.</p>
      )}
      <FormError message={errs._form} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          {app.installed ? "Save" : "Install"}
        </Button>
      </div>
    </form>
  );
}

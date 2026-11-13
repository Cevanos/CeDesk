"use client";

import { AlertTriangle, ArrowLeft, CheckCircle2, FileUp, Loader2, RefreshCw } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { PasswordInput } from "@/components/auth/password-input";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
} from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { FormError, LoadError } from "@/components/workspace/list-shared";
import {
  useCloseImportConnection,
  useConnectImport,
  useDiscoverImport,
  useDryRunImport,
  useImportHistory,
  useImportRun,
  useStartImport,
  useSyncNow,
  useSyncStop,
  useTeams,
  useUploadImportCsv,
} from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { ImportKind, ImportRun } from "@/lib/api/types";
import { cn, formatDateTime } from "@/lib/utils";

interface FieldDef {
  key: string;
  label: string;
  hint?: string;
  options?: string[];
  optional?: boolean;
}
interface SourceDef {
  id: string;
  label: string;
  settings: FieldDef[];
  credentials: FieldDef[];
  help: string;
}

export const SOURCES: SourceDef[] = [
  {
    id: "intercom",
    label: "Intercom",
    settings: [{ key: "region", label: "Region", options: ["us", "eu", "au"] }],
    credentials: [{ key: "token", label: "Access token" }],
    help: "An access token with permission to read contacts, conversations and articles.",
  },
  {
    id: "zendesk",
    label: "Zendesk",
    settings: [{ key: "subdomain", label: "Subdomain", hint: "acme for acme.zendesk.com" }],
    credentials: [
      { key: "email", label: "Agent email" },
      { key: "token", label: "API token" },
    ],
    help: "An agent email and an API token from Admin Center.",
  },
  {
    id: "freshdesk",
    label: "Freshdesk",
    settings: [{ key: "domain", label: "Domain", hint: "acme for acme.freshdesk.com" }],
    credentials: [{ key: "api_key", label: "API key" }],
    help: "The API key from your Freshdesk profile settings.",
  },
  {
    id: "gorgias",
    label: "Gorgias",
    settings: [{ key: "domain", label: "Domain", hint: "acme for acme.gorgias.com" }],
    credentials: [
      { key: "email", label: "Email" },
      { key: "api_key", label: "API key" },
    ],
    help: "Your login email and a REST API key.",
  },
  {
    id: "helpscout",
    label: "Help Scout",
    settings: [],
    credentials: [
      { key: "app_id", label: "App ID" },
      { key: "app_secret", label: "App secret" },
      { key: "docs_key", label: "Docs API key (help center)", optional: true },
    ],
    help: "An OAuth app's ID and secret. Add a Docs key to import the help center too.",
  },
  {
    id: "hubspot",
    label: "HubSpot",
    settings: [],
    credentials: [{ key: "token", label: "Private app token" }],
    help: "A private app token. HubSpot's knowledge base isn't imported.",
  },
  {
    id: "zoho",
    label: "Zoho Desk",
    settings: [
      { key: "region", label: "Region", options: ["us", "eu", "in", "au", "jp"] },
      {
        key: "org_id",
        label: "Organization ID",
        optional: true,
        hint: "Only if an error asks for it",
      },
    ],
    credentials: [
      { key: "client_id", label: "Client ID" },
      { key: "client_secret", label: "Client secret" },
      { key: "refresh_token", label: "Refresh token" },
    ],
    help: "A self client's ID, secret and a refresh token.",
  },
];

const KINDS: { id: ImportKind; label: string }[] = [
  { id: "contacts", label: "Contacts" },
  { id: "tickets", label: "Tickets" },
  { id: "articles", label: "Help articles" },
  { id: "replies", label: "Saved replies" },
];

const label = (s: string) =>
  SOURCES.find((x) => x.id === s)?.label ?? (s === "csv" ? "CSV file" : s);

type Stage =
  | { name: "source" }
  | { name: "connect"; source: SourceDef | "csv" }
  | { name: "plan"; connectionId: string; source: string; account: string | null }
  | { name: "run"; runId: string; connectionId: string | null };

export function ImportsView() {
  const [tab, setTab] = useState<"new" | "history">("new");
  return (
    <div className="space-y-5">
      <div>
        <h2 className="hidden text-lg font-semibold tracking-tight lg:block">Imports</h2>
        <p className="text-sm text-muted-foreground">
          Bring contacts, tickets, help articles and saved replies over from another help desk.
        </p>
      </div>
      <div
        role="tablist"
        aria-label="Imports"
        className="inline-flex rounded-xl border border-border bg-surface-2 p-1"
      >
        {(["new", "history"] as const).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={cn(
              "rounded-lg px-3 py-1.5 text-[13px] font-semibold transition-all",
              tab === t
                ? "bg-surface text-foreground shadow-card ring-1 ring-border"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t === "new" ? "New import" : "History"}
          </button>
        ))}
      </div>
      {tab === "new" ? <Wizard onHistory={() => setTab("history")} /> : <History />}
    </div>
  );
}

function Wizard({ onHistory }: { onHistory: () => void }) {
  const [stage, setStage] = useState<Stage>({ name: "source" });
  const reset = () => setStage({ name: "source" });

  if (stage.name === "source") {
    return (
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[...SOURCES, null].map((s) => (
          <button
            key={s?.id ?? "csv"}
            type="button"
            onClick={() => setStage({ name: "connect", source: s ?? "csv" })}
            className="rounded-2xl border border-border bg-surface p-4 text-left shadow-card transition-all hover:-translate-y-0.5 hover:border-border-strong hover:shadow-pop"
          >
            <span className="block font-semibold">{s?.label ?? "CSV file"}</span>
            <span className="mt-1 block text-xs text-muted-foreground">
              {s ? "Connect with a key" : "Upload a spreadsheet"}
            </span>
          </button>
        ))}
      </div>
    );
  }
  if (stage.name === "connect") {
    return stage.source === "csv" ? (
      <CsvStep
        onBack={reset}
        onDone={(c) =>
          setStage({ name: "plan", connectionId: c.id, source: "csv", account: c.label ?? null })
        }
      />
    ) : (
      <ConnectStep
        def={stage.source}
        onBack={reset}
        onDone={(c) =>
          setStage({
            name: "plan",
            connectionId: c.id,
            source: stage.source === "csv" ? "csv" : stage.source.id,
            account: c.label ?? null,
          })
        }
      />
    );
  }
  if (stage.name === "plan") {
    return (
      <PlanStep
        key={stage.connectionId}
        connectionId={stage.connectionId}
        source={stage.source}
        account={stage.account}
        onBack={reset}
        onStarted={(runId) => setStage({ name: "run", runId, connectionId: stage.connectionId })}
      />
    );
  }
  return (
    <RunStep
      runId={stage.runId}
      connectionId={stage.connectionId}
      onAgain={reset}
      onHistory={onHistory}
    />
  );
}

function StepHeader({ title, onBack }: { title: string; onBack: () => void }) {
  return (
    <div className="flex items-center gap-2">
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ArrowLeft className="size-4" /> Back
      </Button>
      <h3 className="text-base font-semibold">{title}</h3>
    </div>
  );
}

function ConnectStep({
  def,
  onBack,
  onDone,
}: {
  def: SourceDef;
  onBack: () => void;
  onDone: (c: { id: string; label?: string | null }) => void;
}) {
  const connect = useConnectImport();
  const [settings, setSettings] = useState<Record<string, string>>(() =>
    Object.fromEntries(def.settings.filter((f) => f.options).map((f) => [f.key, f.options![0]!])),
  );
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const missing = [...def.settings, ...def.credentials].some(
    (f) => !f.optional && !(f.key in creds ? creds[f.key] : settings[f.key])?.trim(),
  );

  return (
    <Card className="max-w-xl space-y-4 p-5">
      <StepHeader title={`Connect ${def.label}`} onBack={onBack} />
      <p className="text-sm text-muted-foreground">{def.help}</p>
      <form
        className="space-y-3"
        noValidate
        autoComplete="off"
        onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          const clean = (o: Record<string, string>) =>
            Object.fromEntries(
              Object.entries(o)
                .filter(([, v]) => v.trim())
                .map(([k, v]) => [k, v.trim()]),
            );
          connect.mutate(
            { source: def.id, settings: clean(settings), credentials: clean(creds) },
            {
              onSuccess: (c) => {
                toast.success(`Connected to ${c.label ?? def.label}`);
                onDone(c);
              },
              onError: (e) => setError(e instanceof ApiError ? e.userMessage : "Couldn't connect."),
            },
          );
        }}
      >
        {def.settings.map((f) => (
          <Field key={f.key} label={f.label} htmlFor={`imp-${f.key}`} hint={f.hint}>
            {f.options ? (
              <Select
                id={`imp-${f.key}`}
                value={settings[f.key] ?? f.options[0]}
                options={f.options.map((o) => ({ value: o, label: o.toUpperCase() }))}
                onChange={(e) => setSettings((s) => ({ ...s, [f.key]: e.target.value }))}
              />
            ) : (
              <Input
                id={`imp-${f.key}`}
                value={settings[f.key] ?? ""}
                onChange={(e) => setSettings((s) => ({ ...s, [f.key]: e.target.value }))}
              />
            )}
          </Field>
        ))}
        {def.credentials.map((f) => (
          <Field key={f.key} label={f.label} htmlFor={`imp-${f.key}`}>
            <PasswordInput
              id={`imp-${f.key}`}
              autoComplete="off"
              value={creds[f.key] ?? ""}
              onChange={(e) => setCreds((s) => ({ ...s, [f.key]: e.target.value }))}
            />
          </Field>
        ))}
        <p className="text-xs text-muted-foreground">
          Keys are stored encrypted and never shown again; only the last 4 characters are.
        </p>
        <FormError message={error ?? undefined} />
        <Button type="submit" disabled={missing} loading={connect.isPending}>
          Connect
        </Button>
      </form>
    </Card>
  );
}

function CsvStep({
  onBack,
  onDone,
}: {
  onBack: () => void;
  onDone: (c: { id: string; label?: string | null }) => void;
}) {
  const upload = useUploadImportCsv();
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);

  return (
    <Card className="max-w-xl space-y-4 p-5">
      <StepHeader title="Upload a CSV file" onBack={onBack} />
      <p className="text-sm text-muted-foreground">
        A UTF-8 CSV file up to 25 MB, with column names in the first row. If a required column is
        missing, we&apos;ll tell you which.
      </p>
      <input
        ref={input}
        type="file"
        accept=".csv,text/csv"
        aria-label="CSV file"
        className="block w-full text-sm file:mr-3 file:rounded-lg file:border file:border-border file:bg-surface-2 file:px-3 file:py-1.5 file:text-sm file:font-medium"
        onChange={() => setError(null)}
      />
      <FormError message={error ?? undefined} />
      <Button
        loading={upload.isPending}
        onClick={() => {
          const file = input.current?.files?.[0];
          if (!file) return setError("Choose a file first.");
          if (file.size > 25 * 1024 * 1024) return setError("That file is larger than 25 MB.");
          upload.mutate(file, {
            onSuccess: onDone,
            onError: (e) =>
              setError(
                e instanceof ApiError && e.status === 413
                  ? "That file is larger than 25 MB."
                  : e instanceof ApiError
                    ? e.userMessage
                    : "Upload failed.",
              ),
          });
        }}
      >
        <FileUp className="size-4" /> Upload
      </Button>
    </Card>
  );
}

function PlanStep({
  connectionId,
  source,
  account,
  onBack,
  onStarted,
}: {
  connectionId: string;
  source: string;
  account: string | null;
  onBack: () => void;
  onStarted: (runId: string) => void;
}) {
  const discover = useDiscoverImport(connectionId);
  const teams = useTeams();
  const dry = useDryRunImport(connectionId);
  const start = useStartImport(connectionId);
  const [kinds, setKinds] = useState<ImportKind[]>(KINDS.map((k) => k.id));
  const [teamMap, setTeamMap] = useState<Record<string, string | null> | null>(null);
  const [sync, setSync] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (discover.isPending) return <Skeleton className="h-48" />;
  if (discover.isError)
    return (
      <>
        <StepHeader title="Look at the source" onBack={onBack} />
        <LoadError error={discover.error} />
      </>
    );
  const d = discover.data;
  const map = teamMap ?? Object.fromEntries(d.groups.map((g) => [g.id, g.suggested_team_id]));
  const opts = { kinds, team_map: map };
  const teamOptions = [
    { value: "", label: "No team" },
    ...(teams.data ?? []).map((t) => ({ value: t.id, label: t.name })),
  ];

  return (
    <div className="space-y-4">
      <StepHeader title={`Import from ${account ?? label(source)}`} onBack={onBack} />
      <Card className="space-y-4 p-5">
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          {Object.entries(d.counts).map(([k, n]) => (
            <div key={k} className="rounded-lg bg-surface-2 p-3">
              <dt className="text-xs text-muted-foreground capitalize">{k.replace(/_/g, " ")}</dt>
              <dd className="text-lg font-semibold tabular-nums">{n.toLocaleString()}</dd>
            </div>
          ))}
        </dl>
        <Warnings items={d.warnings} />

        <fieldset>
          <legend className="text-[13px] font-medium text-foreground-2">What to import</legend>
          <div className="mt-1.5 flex flex-wrap gap-3">
            {KINDS.map((k) => (
              <label key={k.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={kinds.includes(k.id)}
                  onChange={(e) =>
                    setKinds(e.target.checked ? [...kinds, k.id] : kinds.filter((x) => x !== k.id))
                  }
                />
                {k.label}
              </label>
            ))}
          </div>
        </fieldset>

        {d.groups.length > 0 && (
          <div>
            <h4 className="text-[13px] font-medium text-foreground-2">
              Which team gets each group
            </h4>
            <ul className="mt-2 divide-y divide-border rounded-lg border border-border">
              {d.groups.map((g) => (
                <li key={g.id} className="flex flex-wrap items-center gap-3 px-3 py-2">
                  <span className="min-w-0 flex-1 truncate text-sm">{g.name}</span>
                  <div className="w-52">
                    <Select
                      aria-label={`Team for ${g.name}`}
                      value={map[g.id] ?? ""}
                      options={teamOptions}
                      onChange={(e) => setTeamMap({ ...map, [g.id]: e.target.value || null })}
                    />
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {d.status_map.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Statuses: {d.status_map.map((s) => `${s.from} → ${s.to}`).join(", ")}
          </p>
        )}
      </Card>

      {dry.data && (
        <Card className="space-y-2 p-5" aria-live="polite">
          <h4 className="text-sm font-semibold">Dry run: nothing was saved</h4>
          <ul className="grid gap-1 text-sm sm:grid-cols-2">
            {Object.entries(dry.data.will_create).map(([k, n]) => (
              <li key={k} className="capitalize">
                {k}: <strong>{n.toLocaleString()}</strong> to create
                {dry.data.already_imported[k]
                  ? `, ${dry.data.already_imported[k]!.toLocaleString()} already imported`
                  : ""}
              </li>
            ))}
          </ul>
          <Warnings items={dry.data.warnings} />
        </Card>
      )}

      <Card className="flex items-center justify-between gap-4 p-4">
        <div>
          <p id="imp-sync" className="text-sm font-medium">
            Keep syncing until we go live
          </p>
          <p id="imp-sync-d" className="text-xs text-muted-foreground">
            Looks for new and changed tickets every 5 minutes. The stored key is kept until you
            stop.
          </p>
        </div>
        <Switch checked={sync} onChange={setSync} labelledBy="imp-sync" describedBy="imp-sync-d" />
      </Card>

      <FormError message={error ?? undefined} />
      <div className="flex flex-wrap justify-end gap-3">
        <Button
          variant="outline"
          disabled={kinds.length === 0}
          loading={dry.isPending}
          onClick={() => {
            setError(null);
            dry.mutate(opts, {
              onError: (e) => setError(e instanceof ApiError ? e.userMessage : "Dry run failed."),
            });
          }}
        >
          Dry run
        </Button>
        <Button
          disabled={kinds.length === 0}
          loading={start.isPending}
          onClick={() => {
            setError(null);
            start.mutate(
              { ...opts, sync },
              {
                onSuccess: (r) => onStarted(r.id),
                onError: (e) =>
                  setError(e instanceof ApiError ? e.userMessage : "Couldn't start the import."),
              },
            );
          }}
        >
          Start import
        </Button>
      </div>
    </div>
  );
}

function Warnings({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul
      role="note"
      className="space-y-1 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning"
    >
      {items.map((w) => (
        <li key={w} className="flex gap-2">
          <AlertTriangle className="mt-0.5 size-3.5 flex-none" aria-hidden />
          {w}
        </li>
      ))}
    </ul>
  );
}

const num = (v: unknown) => (typeof v === "number" ? v : 0);

export function CountsList({ run }: { run: ImportRun }) {
  const rows = Object.entries(run.counts).filter(([k]) => k !== "sync");
  if (rows.length === 0) return null;
  return (
    <ul className="grid gap-1 text-sm sm:grid-cols-2">
      {rows.map(([k, v]) => (
        <li key={k} className="capitalize">
          {k.replace(/_/g, " ")}:{" "}
          {typeof v === "number" ? (
            <strong>{v.toLocaleString()}</strong>
          ) : (
            Object.entries(v)
              .map(([n, c]) => `${c.toLocaleString()} ${n.replace(/_/g, " ")}`)
              .join(" · ")
          )}
        </li>
      ))}
    </ul>
  );
}

function RunStep({
  runId,
  connectionId,
  onAgain,
  onHistory,
}: {
  runId: string;
  connectionId: string | null;
  onAgain: () => void;
  onHistory: () => void;
}) {
  const q = useImportRun(runId);
  const syncNow = useSyncNow(runId);
  const syncStop = useSyncStop(runId);
  const close = useCloseImportConnection(connectionId ?? "");
  const [closed, setClosed] = useState(false);

  if (q.isPending) return <Skeleton className="h-40" />;
  if (q.isError) return <LoadError error={q.error} />;
  const r = q.data;
  const busy = r.state === "queued" || r.state === "running";
  const sync = r.counts.sync;

  return (
    <Card className="space-y-4 p-5" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold">Import from {r.label ?? label(r.source)}</h3>
        <RunBadge run={r} />
      </div>
      {busy && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Importing… this page updates by
          itself.
        </p>
      )}
      <CountsList run={r} />

      {r.state === "failed" && (
        <>
          <FormError message={r.last_error ?? "The import failed."} />
          <p className="text-xs text-muted-foreground">
            Start again to retry. What was imported already is skipped.
          </p>
          <Button onClick={onAgain}>Start again</Button>
        </>
      )}

      {r.state === "done" && r.sync_on && (
        <div className="space-y-3 rounded-xl bg-surface-2 p-4">
          <p className="text-sm">
            Syncing every 5 minutes
            {r.last_sync_at ? ` · last look ${formatDateTime(r.last_sync_at)}` : ""}
          </p>
          {typeof sync === "object" && (
            <p className="text-sm text-muted-foreground">
              {num(sync.created)} created · {num(sync.updated)} updated · {num(sync.new_messages)}{" "}
              new messages
            </p>
          )}
          {r.last_error && (
            <Warnings items={[`${r.last_error} The sync keeps trying every hour.`]} />
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              loading={syncNow.isPending}
              onClick={() =>
                syncNow.mutate(undefined, {
                  onSuccess: () => toast.success("Sync started"),
                  onError: (e) =>
                    toast.error(e instanceof ApiError ? e.userMessage : "Couldn't sync"),
                })
              }
            >
              <RefreshCw className="size-4" /> Sync now
            </Button>
            <Button
              variant="outline"
              loading={syncStop.isPending}
              onClick={() => {
                if (!confirm("We're live: stop syncing? This deletes the stored key.")) return;
                syncStop.mutate(undefined, {
                  onSuccess: () => toast.success("Syncing stopped"),
                  onError: (e) =>
                    toast.error(e instanceof ApiError ? e.userMessage : "Couldn't stop"),
                });
              }}
            >
              We&apos;re live: stop syncing
            </Button>
          </div>
        </div>
      )}

      {r.state === "done" && !r.sync_on && connectionId && !closed && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm text-muted-foreground">
            Finished. You can delete the stored key now.
          </p>
          <Button
            variant="outline"
            size="sm"
            loading={close.isPending}
            onClick={() =>
              close.mutate(undefined, {
                onSuccess: () => {
                  setClosed(true);
                  toast.success("Stored key deleted");
                },
                onError: (e) =>
                  toast.error(e instanceof ApiError ? e.userMessage : "Couldn't close"),
              })
            }
          >
            Delete stored key
          </Button>
        </div>
      )}
      {closed && <p className="text-sm text-success">Stored key deleted.</p>}

      {!busy && (
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={onAgain}>
            New import
          </Button>
          <Button variant="ghost" size="sm" onClick={onHistory}>
            See history
          </Button>
        </div>
      )}
    </Card>
  );
}

function RunBadge({ run }: { run: ImportRun }) {
  if (run.state === "failed") return <Badge tone="danger">Failed</Badge>;
  if (run.state === "done") {
    return run.sync_on ? (
      <Badge tone="info">Syncing</Badge>
    ) : (
      <Badge tone="success">
        <CheckCircle2 className="size-3" /> Finished
      </Badge>
    );
  }
  return <Badge tone="primary">Running</Badge>;
}

function History() {
  const q = useImportHistory();
  if (q.isPending) return <Skeleton className="h-40" />;
  if (q.isError) return <LoadError error={q.error} />;
  if (q.data.length === 0) {
    return (
      <Card>
        <EmptyState icon={<FileUp className="size-6" />} title="No imports yet">
          Imports you run will show up here.
        </EmptyState>
      </Card>
    );
  }
  return (
    <Card className="overflow-x-auto">
      <table className="w-full min-w-[46rem] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            {["Source", "Result", "Started by", "Started", "Finished", "Imported", "Error"].map(
              (h) => (
                <th key={h} scope="col" className="px-4 py-2.5 font-medium">
                  {h}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {q.data.map((r) => (
            <tr key={r.id} className="align-top hover:bg-surface-2/60">
              <th scope="row" className="px-4 py-3 text-left font-medium">
                {label(r.source)}
                {r.label && (
                  <span className="block text-xs font-normal text-muted-foreground">{r.label}</span>
                )}
              </th>
              <td className="px-4 py-3">
                <RunBadge run={r} />
              </td>
              <td className="px-4 py-3">{r.started_by ?? "—"}</td>
              <td className="px-4 py-3">{r.started_at ? formatDateTime(r.started_at) : "—"}</td>
              <td className="px-4 py-3">{r.finished_at ? formatDateTime(r.finished_at) : "—"}</td>
              <td className="px-4 py-3">
                <span className="block text-xs text-muted-foreground">{r.kinds.join(", ")}</span>
                <CountsList run={r} />
              </td>
              <td className="px-4 py-3 text-xs text-danger">{r.last_error ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

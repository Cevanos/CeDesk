"use client";

import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  FileSpreadsheet,
  FileUp,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { useRef, useState } from "react";
import { SiHelpscout, SiHubspot, SiIntercom, SiZendesk, SiZoho } from "react-icons/si";
import { toast } from "sonner";

import { PasswordInput } from "@/components/auth/password-input";
import { useConfirm } from "@/components/ui/confirm";
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
import {
  conflictMessage,
  FailuresList,
  RunReport,
  UpdateCredentials,
} from "@/components/workspace/imports-run-extras";
import { FormError, LoadError } from "@/components/workspace/list-shared";
import {
  useCloseImportConnection,
  useConnectImport,
  useDiscoverImport,
  useDryRunImport,
  useImportHistory,
  useImportRun,
  useResumeImport,
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
  /** Alternative ways to sign in (Zendesk). The first is the default and replaces `credentials`. */
  authModes?: { id: string; label: string; credentials: FieldDef[]; help?: string }[];
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
      { key: "client_id", label: "Client ID" },
      { key: "client_secret", label: "Client secret" },
    ],
    help: "Connect with an OAuth client (recommended) or an agent email and API token. Zendesk retires API tokens on 30 Apr 2027.",
    authModes: [
      {
        id: "oauth",
        label: "OAuth client (recommended)",
        credentials: [
          { key: "client_id", label: "Client ID" },
          { key: "client_secret", label: "Client secret" },
        ],
        help: "Admin Center › Apps and integrations › APIs › OAuth clients › Add OAuth client. Kind: Confidential, scope: read.",
      },
      {
        id: "token",
        label: "API token",
        credentials: [
          { key: "email", label: "Agent email" },
          { key: "token", label: "API token" },
        ],
        help: "An agent email and an API token from Admin Center.",
      },
    ],
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
    credentials: [{ key: "token", label: "Private app token or service key" }],
    help: "Required scopes: crm.objects.tickets.read, crm.objects.contacts.read, crm.objects.owners.read, sales-email-read, files.read. Optional: calls, meetings and conversations.read (chat threads). HubSpot's knowledge base isn't imported.",
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
    help: "A self client's ID, secret and a refresh token. Scopes: Desk.tickets.READ, Desk.contacts.READ, Desk.basic.READ, Desk.articles.READ (plus Desk.search.READ for archived tickets).",
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

const BRAND: Record<string, { color: string; Icon: React.ElementType }> = {
  intercom: { color: "#1F8DED", Icon: SiIntercom },
  zendesk: { color: "#03363D", Icon: SiZendesk },
  freshdesk: { color: "#25C16F", Icon: FreshdeskMark },
  gorgias: { color: "#E8505B", Icon: GorgiasMark },
  helpscout: { color: "#1292EE", Icon: SiHelpscout },
  hubspot: { color: "#FF7A59", Icon: SiHubspot },
  zoho: { color: "#E42527", Icon: SiZoho },
};

/** Not in the simple-icons set, so drawn here as simple marks in the brand colour. */
function FreshdeskMark(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <path d="M12 2a9 9 0 0 0-9 9v5a3 3 0 0 0 3 3h1v-7H5v-1a7 7 0 1 1 14 0v1h-2v7h1a3 3 0 0 0 3-3v-5a9 9 0 0 0-9-9Zm-1 17h2a2 2 0 0 1 2 2v1h-6v-1a2 2 0 0 1 2-2Z" />
    </svg>
  );
}
function GorgiasMark(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <path d="M12 2C6.5 2 2 6.2 2 11.5S6.5 21 12 21c1.5 0 2.9-.3 4.2-.9L21 22l-1.6-4.2A9.2 9.2 0 0 0 22 11.5C22 6.2 17.5 2 12 2Zm.2 4.5c1.6 0 3 .6 4 1.6l-1.5 1.6c-.7-.6-1.5-.9-2.5-.9-2 0-3.5 1.5-3.5 3.4s1.500 3.4 3.500 3.400c1.300 0 2.200-.5 2.700-1.300h-2.800v-2.100h5.200c.1.400.1.700.1 1.100 0 3-2.200 5.200-5.200 5.200S7 15.200 7 11.900s2.300-5.400 5.200-5.400Z" />
    </svg>
  );
}

export function SourceIcon({ id, className }: { id: string; className?: string }) {
  const b = BRAND[id];
  const Icon = b?.Icon ?? FileSpreadsheet;
  return (
    <span
      aria-hidden
      className={cn(
        "grid size-10 flex-none place-items-center rounded-xl border border-border bg-white",
        className,
      )}
    >
      <Icon className="size-[55%]" style={{ color: b?.color ?? "#16A34A" }} />
    </span>
  );
}

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
            className="flex items-center gap-3 rounded-2xl border border-border bg-surface p-4 text-left shadow-card transition-all hover:-translate-y-0.5 hover:border-border-strong hover:shadow-pop"
          >
            <SourceIcon id={s?.id ?? "csv"} />
            <span className="min-w-0">
              <span className="block font-semibold">{s?.label ?? "CSV file"}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {s ? "Connect with a key" : "Upload a spreadsheet"}
              </span>
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

function StepHeader({
  title,
  onBack,
  icon,
}: {
  title: string;
  onBack: () => void;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ArrowLeft className="size-4" /> Back
      </Button>
      {icon}
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
  const [settings, setSettings] = useState<Record<string, string>>(() => ({
    ...Object.fromEntries(
      def.settings.filter((f) => f.options).map((f) => [f.key, f.options![0]!]),
    ),
    // Freshdesk imports archived tickets unless told otherwise.
    ...(def.id === "freshdesk" ? { include_archived: "true" } : {}),
  }));
  const [mode, setMode] = useState(def.authModes?.[0]?.id ?? "");
  const activeMode = def.authModes?.find((m) => m.id === mode);
  const credFields = activeMode?.credentials ?? def.credentials;
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const missing = [...def.settings, ...credFields].some(
    (f) => !f.optional && !(f.key in creds ? creds[f.key] : settings[f.key])?.trim(),
  );

  return (
    <Card className="max-w-xl space-y-4 p-5">
      <StepHeader
        title={`Connect ${def.label}`}
        onBack={onBack}
        icon={<SourceIcon id={def.id} className="size-8" />}
      />
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
          const sent = clean(settings);
          // Zoho: unchecked simply means the key is left out.
          if (def.id === "zoho" && sent.include_archived !== "true") delete sent.include_archived;
          connect.mutate(
            { source: def.id, settings: sent, credentials: clean(creds) },
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
        {def.authModes && (
          <fieldset>
            <legend className="text-[13px] font-medium text-foreground-2">How to connect</legend>
            <div className="mt-1.5 flex flex-wrap gap-3">
              {def.authModes.map((m) => (
                <label key={m.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name="imp-auth-mode"
                    checked={mode === m.id}
                    onChange={() => {
                      setMode(m.id);
                      setCreds({});
                    }}
                  />
                  {m.label}
                </label>
              ))}
            </div>
            {activeMode?.help && (
              <p className="mt-1.5 text-xs text-muted-foreground">{activeMode.help}</p>
            )}
          </fieldset>
        )}
        <CredentialFields fields={credFields} creds={creds} setCreds={setCreds} />
        {(def.id === "freshdesk" || def.id === "zoho") && (
          <div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={settings.include_archived === "true"}
                onChange={(e) =>
                  setSettings((s) => ({
                    ...s,
                    // Freshdesk needs an explicit "false": a missing key means yes there.
                    include_archived: e.target.checked ? "true" : "false",
                  }))
                }
              />
              Include archived tickets
            </label>
            <p className="mt-1 text-xs text-muted-foreground">
              {def.id === "freshdesk"
                ? "Also import tickets Freshdesk archived (closed over 120 days ago). On by default. Slow on large accounts and uses your Freshdesk API quota."
                : "Also import archived tickets. Needs the Desk.search.READ scope. Zoho lists at most 5,000 archived tickets per department."}
            </p>
          </div>
        )}
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

function CredentialFields({
  fields,
  creds,
  setCreds,
}: {
  fields: FieldDef[];
  creds: Record<string, string>;
  setCreds: React.Dispatch<React.SetStateAction<Record<string, string>>>;
}) {
  return (
    <>
      {fields.map((f) => (
        <Field key={f.key} label={f.label} htmlFor={`imp-${f.key}`}>
          <PasswordInput
            id={`imp-${f.key}`}
            autoComplete="off"
            value={creds[f.key] ?? ""}
            onChange={(e) => setCreds((s) => ({ ...s, [f.key]: e.target.value }))}
          />
        </Field>
      ))}
    </>
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
  const { ask, dialog } = useConfirm();
  const resume = useResumeImport(runId);
  const close = useCloseImportConnection(connectionId ?? "");
  const [closed, setClosed] = useState(false);
  const [stoppedLive, setStoppedLive] = useState(false);
  const [showFailures, setShowFailures] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);

  if (q.isPending) return <Skeleton className="h-40" />;
  if (q.isError) return <LoadError error={q.error} />;
  const r = q.data;
  const busy = r.state === "queued" || r.state === "running";
  const sync = r.counts.sync;

  return (
    <Card className="space-y-4 p-5" aria-live="polite">
      {dialog}
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold">Import from {r.label ?? label(r.source)}</h3>
        <RunBadge run={r} />
      </div>
      {busy && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Importing… this page updates by
          itself.
          {!!r.retry_attempts && r.retry_attempts > 0
            ? ` The source had a temporary error; retrying (try ${r.retry_attempts})…`
            : ""}
        </p>
      )}
      <CountsList run={r} />

      {r.state === "done" && r.report && (
        <RunReport report={r.report} onShowFailures={() => setShowFailures(true)} />
      )}
      {showFailures && <FailuresList runId={runId} />}

      {r.state === "failed" && (
        <>
          <FormError message={resumeError ?? r.last_error ?? "The import failed."} />
          <p className="text-xs text-muted-foreground">
            <strong>Resume</strong> carries on where it stopped. <strong>Start again</strong> reads
            everything again (what is already imported is skipped).
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              loading={resume.isPending}
              onClick={() => {
                setResumeError(null);
                resume.mutate(undefined, {
                  onError: (e) =>
                    setResumeError(e instanceof ApiError ? conflictMessage(e) : "Couldn't resume."),
                });
              }}
            >
              Resume
            </Button>
            <Button variant="outline" onClick={onAgain}>
              Start again
            </Button>
          </div>
          {connectionId && r.source !== "csv" && credentialFields(r.source) && (
            <UpdateCredentials
              connectionId={connectionId}
              fields={credentialFields(r.source)!}
              modes={SOURCES.find((x) => x.id === r.source)?.authModes}
              onUpdated={() => setResumeError(null)}
            />
          )}
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
              onClick={() =>
                ask({
                  title: "Stop syncing?",
                  message: "We're live: syncing stops and the stored key is deleted.",
                  confirmLabel: "Stop syncing",
                  tone: "warning",
                  run: () =>
                    syncStop.mutate(undefined, {
                      onSuccess: () => {
                        setStoppedLive(true);
                        toast.success("Syncing stopped");
                      },
                      onError: (e) =>
                        toast.error(e instanceof ApiError ? e.userMessage : "Couldn't stop"),
                    }),
                })
              }
            >
              We&apos;re live: stop syncing
            </Button>
          </div>
        </div>
      )}

      {r.state === "done" && !r.sync_on && connectionId && !closed && !stoppedLive && (
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
      {(closed || stoppedLive) && (
        <p className="text-sm text-success">
          {stoppedLive
            ? "Syncing stopped. The stored key is already deleted."
            : "Stored key deleted."}
        </p>
      )}

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

/** Fields to re-enter on "Update credentials" (Zendesk also gets a mode choice via `authModes`). */
function credentialFields(source: string): FieldDef[] | null {
  const def = SOURCES.find((s) => s.id === source);
  if (!def) return null;
  return def.authModes?.[0]?.credentials ?? def.credentials;
}

function RunBadge({ run }: { run: ImportRun }) {
  if (run.state === "failed") return <Badge tone="danger">Failed</Badge>;
  if (run.state === "done") {
    const exceptions = run.outcome === "complete_with_exceptions";
    if (run.sync_on) return <Badge tone={exceptions ? "warning" : "info"}>Syncing</Badge>;
    return exceptions ? (
      <Badge tone="warning">
        <AlertTriangle className="size-3" /> Complete, with exceptions
      </Badge>
    ) : (
      <Badge tone="success">
        <CheckCircle2 className="size-3" /> Complete
      </Badge>
    );
  }
  return <Badge tone="primary">Running</Badge>;
}

const pretty = (k: string) => k.replace(/_/g, " ");

/** One panel per imported kind: the headline number (created) and the rest as small chips. */
function KindStats({ run }: { run: ImportRun }) {
  const rows = Object.entries(run.counts).filter(([k]) => k !== "sync");
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">Nothing imported yet.</p>;
  }
  return (
    <div className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
      {rows.map(([k, v]) => {
        const detail = typeof v === "number" ? { total: v } : v;
        const head = typeof v === "number" ? v : (v.created ?? 0);
        const rest = Object.entries(detail).filter(
          ([n]) => typeof v === "number" || n !== "created",
        );
        return (
          <div key={k} className="rounded-xl border border-border bg-surface-2/60 p-3">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                {pretty(k)}
              </span>
              <span className="text-xl font-semibold tabular-nums">{head.toLocaleString()}</span>
            </div>
            {typeof v !== "number" && <p className="text-[11px] text-muted-foreground">created</p>}
            {typeof v !== "number" && rest.length > 0 && (
              <ul className="mt-2 flex flex-wrap gap-1">
                {rest.map(([n, c]) => (
                  <li
                    key={n}
                    className={cn(
                      "rounded-md bg-surface px-1.5 py-0.5 text-[11px] ring-1 ring-border",
                      c === 0 ? "text-muted-foreground/70" : "text-foreground-2",
                    )}
                  >
                    <span className="capitalize">{pretty(n)}</span>{" "}
                    <strong className="font-semibold tabular-nums">{c.toLocaleString()}</strong>
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}

function duration(a?: string | null, b?: string | null) {
  if (!a || !b) return null;
  const sec = Math.max(0, Math.round((new Date(b).getTime() - new Date(a).getTime()) / 1000));
  if (Number.isNaN(sec)) return null;
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  return m < 60 ? `${m}m ${sec % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </dt>
      <dd className="mt-0.5 truncate text-[13px] font-medium">{children}</dd>
    </div>
  );
}

/** Sync now / stop syncing for a live run, straight from its History card. */
function HistorySyncActions({ run }: { run: ImportRun }) {
  const syncNow = useSyncNow(run.id);
  const syncStop = useSyncStop(run.id);
  const { ask, dialog } = useConfirm();
  return (
    <div className="flex flex-wrap gap-2">
      {dialog}
      <Button
        variant="outline"
        size="sm"
        loading={syncNow.isPending}
        onClick={() =>
          syncNow.mutate(undefined, {
            onSuccess: () => toast.success("Sync started"),
            onError: (e) => toast.error(e instanceof ApiError ? e.userMessage : "Couldn't sync"),
          })
        }
      >
        <RefreshCw className="size-4" /> Sync now
      </Button>
      <Button
        variant="outline"
        size="sm"
        loading={syncStop.isPending}
        onClick={() =>
          ask({
            title: "Stop syncing?",
            message: "We're live: syncing stops and the stored key is deleted.",
            confirmLabel: "Stop syncing",
            tone: "warning",
            run: () =>
              syncStop.mutate(undefined, {
                onSuccess: () => toast.success("Syncing stopped"),
                onError: (e) =>
                  toast.error(e instanceof ApiError ? e.userMessage : "Couldn't stop"),
              }),
          })
        }
      >
        We&apos;re live: stop syncing
      </Button>
    </div>
  );
}

function History() {
  const q = useImportHistory();
  if (q.isPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-40" />
        <Skeleton className="h-40" />
      </div>
    );
  }
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
    <ul className="space-y-3">
      {q.data.map((r) => {
        const took = duration(r.started_at, r.finished_at);
        return (
          <li key={r.id}>
            <Card className="space-y-4 p-4 sm:p-5">
              <div className="flex flex-wrap items-center gap-3">
                <SourceIcon id={r.source} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-[15px] font-semibold">{label(r.source)}</h3>
                    <RunBadge run={r} />
                  </div>
                  {r.label && <p className="truncate text-xs text-muted-foreground">{r.label}</p>}
                </div>
                {r.kinds.length > 0 && (
                  <ul className="flex flex-wrap gap-1">
                    {r.kinds.map((k) => (
                      <li
                        key={k}
                        className="rounded-full bg-primary-soft px-2 py-0.5 text-[11px] font-medium text-primary capitalize"
                      >
                        {k}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-y border-border py-3 sm:grid-cols-4">
                <Meta label="Started by">{r.started_by ?? "—"}</Meta>
                <Meta label="Started">{r.started_at ? formatDateTime(r.started_at) : "—"}</Meta>
                <Meta label="Finished">{r.finished_at ? formatDateTime(r.finished_at) : "—"}</Meta>
                <Meta label="Duration">{took ?? "—"}</Meta>
              </dl>

              <KindStats run={r} />

              {r.sync_on && r.source !== "csv" && <HistorySyncActions run={r} />}

              {r.last_error && (
                <p
                  role="alert"
                  className="flex gap-2 rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger"
                >
                  <AlertTriangle className="mt-0.5 size-3.5 flex-none" aria-hidden />
                  {r.last_error}
                </p>
              )}
            </Card>
          </li>
        );
      })}
    </ul>
  );
}

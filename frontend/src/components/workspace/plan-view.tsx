"use client";

import { Download } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Card, Field, Input, Skeleton } from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import {
  parseDays,
  retentionExportPath,
  useArchiveSettings,
  useRetention,
  useRetentionExports,
  useSaveArchiveSettings,
  useSaveRetention,
  useUsage,
  type ArchiveSettings,
  type RetentionExport,
  type RetentionSettings,
} from "@/hooks/use-workspace";
import { ApiError, downloadFile } from "@/lib/api/client";
import { formatBytes } from "@/lib/format";

export function PlanView() {
  return (
    <div className="space-y-6">
      <UsageCard />
      <RetentionCard />
      <ArchiveCard />
      <ExportsCard />
    </div>
  );
}

function Bar({
  label,
  used,
  limit,
  format = String,
}: {
  label: string;
  used: number;
  limit: number | null;
  format?: (n: number) => string;
}) {
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  return (
    <li>
      <div className="flex justify-between text-sm">
        <span>{label}</span>
        <span className="text-muted-foreground tabular-nums">
          {format(used)}
          {limit ? ` / ${format(limit)}` : " (no limit)"}
        </span>
      </div>
      {limit ? (
        <div
          role="progressbar"
          aria-label={label}
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-3"
        >
          <div
            className={`h-full ${pct >= 90 ? "bg-danger" : "bg-primary"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      ) : null}
    </li>
  );
}

function UsageCard() {
  const q = useUsage();
  if (q.isPending) return <Skeleton className="h-32" />;
  if (q.isError) return <LoadError error={q.error} />;
  const u = q.data;
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-[15px] font-semibold">Plan and usage</h2>
        <Badge tone="primary">{u.plan ?? "No plan limits"}</Badge>
      </div>
      <ul className="space-y-3">
        <Bar label="Agents" used={u.agents} limit={u.limits.agents} />
        <Bar
          label="Tickets this month"
          used={u.tickets_this_month}
          limit={u.limits.tickets_month}
        />
        <Bar
          label="File storage"
          used={u.storage_bytes}
          limit={u.limits.storage_bytes}
          format={formatBytes}
        />
      </ul>
    </Card>
  );
}

function RetentionCard() {
  const q = useRetention();
  if (q.isPending) return <Skeleton className="h-32" />;
  if (q.isError) return <LoadError error={q.error} />;
  return <RetentionForm initial={q.data} />;
}

function RetentionForm({ initial }: { initial: RetentionSettings }) {
  const save = useSaveRetention();
  const [days, setDays] = useState(
    initial.closed_ticket_days ? String(initial.closed_ticket_days) : "",
  );
  const [exportFirst, setExportFirst] = useState(initial.export_before_delete);
  const [errs, setErrs] = useState<Record<string, string>>({});
  return (
    <Card className="p-5">
      <h2 className="text-[15px] font-semibold">Retention</h2>
      <form
        noValidate
        className="mt-3 space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          const n = days.trim() ? parseDays(days) : null;
          if (days.trim() && (n === null || n < 30 || n > 3650))
            return setErrs({
              closed_ticket_days: "Enter 30 to 3650 days, or leave empty to keep resolved tickets.",
            });
          setErrs({});
          save.mutate(
            { closed_ticket_days: n, export_before_delete: exportFirst },
            {
              onSuccess: () => toast.success("Retention saved"),
              // One message, in one place: the banner.
              onError: (err) =>
                setErrs({
                  _form:
                    fieldErrors(err)._form ||
                    (err instanceof ApiError ? err.message : "Couldn't save retention."),
                }),
            },
          );
        }}
      >
        <Field
          label="Delete resolved tickets after (days)"
          htmlFor="ret-days"
          error={errs.closed_ticket_days}
          hint={`30 to 3650. Leave empty to keep resolved tickets. Deleted tickets can't be brought back. Right now: ${
            initial.effective_days === null ? "kept for good" : `${initial.effective_days} days`
          }.`}
        >
          <Input
            id="ret-days"
            inputMode="numeric"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </Field>
        {initial.plan_history_days !== null && (
          <p className="text-sm text-muted-foreground">
            Your plan keeps history for {initial.plan_history_days} days at most.
          </p>
        )}
        <div className="flex items-center justify-between gap-4">
          <div>
            <p id="ret-exp-l" className="text-sm font-medium">
              Export before delete
            </p>
            <p id="ret-exp-d" className="mt-0.5 text-sm text-muted-foreground">
              Keep a copy of the tickets in a file before they are removed.
            </p>
          </div>
          <Switch
            checked={exportFirst}
            onChange={setExportFirst}
            labelledBy="ret-exp-l"
            describedBy="ret-exp-d"
          />
        </div>
        <FormError message={errs._form} />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            {initial.updated_at
              ? `Last changed${initial.updated_by_name ? ` by ${initial.updated_by_name}` : ""}, ${new Date(initial.updated_at).toLocaleString()}`
              : ""}
          </p>
          <Button type="submit" loading={save.isPending}>
            Save
          </Button>
        </div>
      </form>
    </Card>
  );
}

function ArchiveCard() {
  const q = useArchiveSettings();
  const ret = useRetention();
  if (q.isPending) return <Skeleton className="h-32" />;
  if (q.isError) return <LoadError error={q.error} />;
  const retDays = ret.data?.effective_days;
  return (
    <ArchiveForm
      initial={q.data}
      shortRetentionDays={retDays != null && retDays < q.data.effective_days ? retDays : null}
    />
  );
}

function ArchiveForm({
  initial,
  shortRetentionDays,
}: {
  initial: ArchiveSettings;
  shortRetentionDays: number | null;
}) {
  const save = useSaveArchiveSettings();
  // Empty = the default (null on the wire); the placeholder shows what applies now.
  const [days, setDays] = useState(initial.after_days ? String(initial.after_days) : "");
  const [errs, setErrs] = useState<Record<string, string>>({});
  return (
    <Card className="p-5">
      <h2 className="text-[15px] font-semibold">Archive</h2>
      <form
        noValidate
        className="mt-3 space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          const n = days.trim() ? parseDays(days) : null;
          if (days.trim() && (n === null || n < 30 || n > 3650))
            return setErrs({
              after_days: "Enter 30 to 3650 days, or leave empty for the default.",
            });
          setErrs({});
          save.mutate(
            { after_days: n },
            {
              onSuccess: () => toast.success("Archive age saved"),
              onError: (err) => setErrs(fieldErrors(err)),
            },
          );
        }}
      >
        <Field
          label="Archive resolved tickets after (days)"
          htmlFor="arc-days"
          error={errs.after_days}
          hint={`30 to 3650. Empty uses the default (120). Right now: ${initial.effective_days} days.`}
        >
          <Input
            id="arc-days"
            inputMode="numeric"
            placeholder="120"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </Field>
        {shortRetentionDays !== null && (
          <p role="note" className="rounded-lg bg-surface-2 px-3 py-2 text-sm">
            Resolved tickets are deleted after {shortRetentionDays} days (Retention), so none stay
            long enough to be archived at {initial.effective_days} days.
          </p>
        )}
        <FormError message={errs._form} />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            {initial.updated_at
              ? `Last changed${initial.updated_by_name ? ` by ${initial.updated_by_name}` : ""}, ${new Date(initial.updated_at).toLocaleString()}`
              : ""}
          </p>
          <Button type="submit" loading={save.isPending}>
            Save
          </Button>
        </div>
      </form>
    </Card>
  );
}

function ExportRow({ x }: { x: RetentionExport }) {
  const [busy, setBusy] = useState(false);
  return (
    <tr className="border-t border-border">
      <td className="px-4 py-2.5">{new Date(x.created_at).toLocaleString()}</td>
      <td className="px-4 py-2.5 tabular-nums">{x.tickets}</td>
      <td className="px-4 py-2.5 tabular-nums">{formatBytes(x.size)}</td>
      <td className="px-4 py-2.5">{new Date(x.expires_at).toLocaleDateString()}</td>
      <td className="px-4 py-2.5 text-right">
        <Button
          size="sm"
          variant="outline"
          loading={busy}
          onClick={() => {
            setBusy(true);
            downloadFile(retentionExportPath(x.id), `tickets-export-${x.id}.json`)
              .catch((e) =>
                toast.error(
                  e instanceof ApiError && e.status === 409
                    ? "This file has expired."
                    : e instanceof ApiError
                      ? e.userMessage
                      : "Download failed",
                ),
              )
              .finally(() => setBusy(false));
          }}
        >
          <Download className="size-3.5" /> Download
        </Button>
      </td>
    </tr>
  );
}

function ExportsCard() {
  const q = useRetentionExports();
  const ret = useRetention();
  return (
    <Card className="overflow-hidden">
      <h2 className="px-5 pt-5 pb-2 text-[15px] font-semibold">Retention export files</h2>
      {q.isPending ? (
        <Skeleton className="m-5 h-16" />
      ) : q.isError ? (
        <div className="p-5">
          <LoadError error={q.error} />
        </div>
      ) : q.data.length === 0 ? (
        <p className="px-5 pb-5 text-sm text-muted-foreground">
          {ret.data?.export_before_delete
            ? "No exports yet. A file is written each time retention deletes tickets."
            : "No exports yet. Turn on Export before delete to keep a copy."}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">Made</th>
                <th className="px-4 py-2 font-medium">Tickets</th>
                <th className="px-4 py-2 font-medium">Size</th>
                <th className="px-4 py-2 font-medium">Kept until</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {q.data.map((x) => (
                <ExportRow key={x.id} x={x} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

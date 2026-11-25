"use client";

import { Download } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Card, Field, Input, Skeleton } from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { DataTable, RowTh, TBody, Td, THead, Th, Tr } from "@/components/ui/table";
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
import { bind, fieldError, useFormik } from "@/lib/forms";

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
  const [srv, setSrv] = useState<Record<string, string>>({});
  const form = useFormik({
    initialValues: {
      closed_ticket_days: initial.closed_ticket_days ? String(initial.closed_ticket_days) : "",
      export_before_delete: initial.export_before_delete,
    },
    validate: (v) => {
      const d = v.closed_ticket_days.trim();
      const n = d ? parseDays(d) : null;
      return d && (n === null || n < 30 || n > 3650)
        ? {
            closed_ticket_days: "Enter 30 to 3650 days, or leave empty to keep resolved tickets.",
          }
        : {};
    },
    onSubmit: (v) => {
      setSrv({});
      const d = v.closed_ticket_days.trim();
      save.mutate(
        {
          closed_ticket_days: d ? parseDays(d) : null,
          export_before_delete: v.export_before_delete,
        },
        {
          onSuccess: () => toast.success("Retention saved"),
          // One message, in one place: the banner.
          onError: (err) =>
            setSrv({
              _form:
                fieldErrors(err)._form ||
                (err instanceof ApiError ? err.message : "Couldn't save retention."),
            }),
        },
      );
    },
  });
  const errs = { closed_ticket_days: fieldError(form, "closed_ticket_days"), _form: srv._form };
  return (
    <Card className="p-5">
      <h2 className="text-[15px] font-semibold">Retention</h2>
      <form noValidate className="mt-3 space-y-4" onSubmit={form.handleSubmit}>
        <Field
          label="Delete resolved tickets after (days)"
          htmlFor="ret-days"
          error={errs.closed_ticket_days}
          hint={`30 to 3650. Leave empty to keep resolved tickets. Deleted tickets can't be brought back. Right now: ${
            initial.effective_days === null ? "kept for good" : `${initial.effective_days} days`
          }.`}
        >
          <Input id="ret-days" inputMode="numeric" {...bind(form, "closed_ticket_days")} />
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
            checked={form.values.export_before_delete}
            onChange={(v) => form.setFieldValue("export_before_delete", v)}
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
  const [srv, setSrv] = useState<Record<string, string>>({});
  const form = useFormik({
    initialValues: { after_days: initial.after_days ? String(initial.after_days) : "" },
    validate: (v) => {
      const d = v.after_days.trim();
      const n = d ? parseDays(d) : null;
      return d && (n === null || n < 30 || n > 3650)
        ? { after_days: "Enter 30 to 3650 days, or leave empty for the default." }
        : {};
    },
    onSubmit: (v) => {
      setSrv({});
      const d = v.after_days.trim();
      save.mutate(
        { after_days: d ? parseDays(d) : null },
        {
          onSuccess: () => toast.success("Archive age saved"),
          onError: (err) => setSrv(fieldErrors(err)),
        },
      );
    },
  });
  const errs: Record<string, string | undefined> = {
    after_days: fieldError(form, "after_days") ?? srv.after_days,
    _form: srv._form,
  };
  return (
    <Card className="p-5">
      <h2 className="text-[15px] font-semibold">Archive</h2>
      <form noValidate className="mt-3 space-y-4" onSubmit={form.handleSubmit}>
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
            {...bind(form, "after_days")}
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
    <Tr>
      <RowTh className="font-normal">{new Date(x.created_at).toLocaleString()}</RowTh>
      <Td className="tabular-nums">{x.tickets}</Td>
      <Td className="tabular-nums">{formatBytes(x.size)}</Td>
      <Td>{new Date(x.expires_at).toLocaleDateString()}</Td>
      <Td right>
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
      </Td>
    </Tr>
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
        <DataTable minWidth="32rem">
          <THead>
            <tr>
              <Th>Made</Th>
              <Th>Tickets</Th>
              <Th>Size</Th>
              <Th>Kept until</Th>
              <Th>
                <span className="sr-only">Actions</span>
              </Th>
            </tr>
          </THead>
          <TBody>
            {q.data.map((x) => (
              <ExportRow key={x.id} x={x} />
            ))}
          </TBody>
        </DataTable>
      )}
    </Card>
  );
}

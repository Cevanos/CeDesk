"use client";

import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { PasswordInput } from "@/components/auth/password-input";
import { Badge, Button, Field, Skeleton } from "@/components/ui/primitives";
import { FormError, LoadError } from "@/components/workspace/list-shared";
import {
  useDismissImportFailure,
  useImportFailures,
  useUpdateImportCredentials,
} from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { ImportFailure, ImportReport } from "@/lib/api/types";
import { cn, formatDateTime } from "@/lib/utils";

/** The API's own words for 409s (stored file gone, other account, ...), as the plan asks. */
export const conflictMessage = (e: ApiError) =>
  e.status === 409 && e.message ? e.message : e.userMessage;

const pretty = (k: string) => k.replace(/_/g, " ");

const PROBLEM: Record<string, string> = {
  not_found: "Deleted in the source",
  temporary: "Kept failing",
  incomplete: "Imported without full history",
};

/** Warnings, what was left out, the count check and a link to the failures list. */
export function RunReport({
  report,
  onShowFailures,
}: {
  report: ImportReport;
  onShowFailures: () => void;
}) {
  const recon = Object.entries(report.reconciliation);
  const hasAnything =
    report.warnings.length > 0 ||
    report.losses.length > 0 ||
    recon.length > 0 ||
    report.open_failures > 0;
  if (!hasAnything) return null;
  return (
    <section aria-label="Import report" className="space-y-3 rounded-xl bg-surface-2 p-4">
      <h4 className="text-sm font-semibold">Report</h4>

      {report.warnings.length > 0 && (
        <ul
          role="note"
          className="space-y-1 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning"
        >
          {report.warnings.map((w) => (
            <li key={w} className="flex gap-2">
              <AlertTriangle className="mt-0.5 size-3.5 flex-none" aria-hidden />
              {w}
            </li>
          ))}
        </ul>
      )}

      {report.losses.length > 0 && (
        <div>
          <h5 className="mb-1 text-xs font-medium text-foreground-2">
            What was changed or left out
          </h5>
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 font-medium">What</th>
                <th className="py-1 text-right font-medium">Count</th>
              </tr>
            </thead>
            <tbody>
              {report.losses.map((l) => (
                <tr key={l.key} className="border-t border-border">
                  <td className="py-1.5 pr-3">{l.meaning}</td>
                  <td className="py-1.5 text-right tabular-nums">{l.count.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {recon.length > 0 && (
        <div>
          <h5 className="mb-1 text-xs font-medium text-foreground-2">Count check</h5>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted-foreground">
                <tr>
                  <th className="py-1 pr-3 font-medium">Kind</th>
                  <th className="py-1 pr-3 text-right font-medium">In the source</th>
                  <th className="py-1 pr-3 text-right font-medium">Accounted for</th>
                  <th className="py-1 text-right font-medium">Difference</th>
                </tr>
              </thead>
              <tbody>
                {recon.map(([kind, r]) => (
                  <tr key={kind} className="border-t border-border">
                    <td className="py-1.5 pr-3 capitalize">{pretty(kind)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">
                      {r.in_source == null ? "—" : r.in_source.toLocaleString()}
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">
                      {r.accounted_for.toLocaleString()}
                    </td>
                    <td
                      className={cn(
                        "py-1.5 text-right tabular-nums",
                        r.difference != null && r.difference > 0 && "font-semibold text-danger",
                      )}
                    >
                      {r.difference == null ? "—" : r.difference.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {report.open_failures > 0 && (
        <p className="text-sm">
          {report.open_failures.toLocaleString()} record{report.open_failures === 1 ? "" : "s"}{" "}
          could not be imported.{" "}
          <button
            type="button"
            onClick={onShowFailures}
            className="font-medium text-primary-ink hover:underline"
          >
            See which
          </button>
        </p>
      )}
    </section>
  );
}

function FailureRow({ f, runId }: { f: ImportFailure; runId: string }) {
  const dismiss = useDismissImportFailure(runId);
  return (
    <tr className="border-t border-border align-top">
      <td className="py-2 pr-3 capitalize">{f.kind}</td>
      <td className="py-2 pr-3 font-mono text-xs break-all">{f.external_id}</td>
      <td className="py-2 pr-3">{PROBLEM[f.category] ?? pretty(f.category)}</td>
      <td className="py-2 pr-3 text-xs text-muted-foreground">{f.last_error ?? "—"}</td>
      <td className="py-2 pr-3 text-right tabular-nums">{f.attempts}</td>
      <td className="py-2 pr-3 text-xs whitespace-nowrap">
        {f.last_failed_at ? formatDateTime(f.last_failed_at) : "—"}
      </td>
      <td className="py-2 text-right">
        {f.status === "open" ? (
          <Button
            variant="outline"
            size="sm"
            loading={dismiss.isPending}
            onClick={() => {
              if (!confirm("Accept that this record is missing?")) return;
              dismiss.mutate(f.id, {
                onSuccess: () => toast.success("Dismissed"),
                onError: (e) =>
                  toast.error(
                    e instanceof ApiError && e.status === 409
                      ? "That record is no longer open."
                      : e instanceof ApiError
                        ? e.userMessage
                        : "Couldn't dismiss",
                  ),
              });
            }}
          >
            Dismiss
          </Button>
        ) : (
          <Badge tone={f.status === "resolved" ? "success" : "neutral"} className="capitalize">
            {f.status}
          </Badge>
        )}
      </td>
    </tr>
  );
}

/** Records the import could not bring over, with "Dismiss" for each open one. */
export function FailuresList({ runId }: { runId: string }) {
  const q = useImportFailures(runId, true);
  if (q.isPending) return <Skeleton className="h-24" />;
  if (q.isError) return <LoadError error={q.error} />;
  const rows = q.data.pages.flatMap((p) => p.failures);
  return (
    <section aria-label="Not imported" className="space-y-2 rounded-xl border border-border p-4">
      <h4 className="text-sm font-semibold">Not imported</h4>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing is missing.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 font-medium">Type</th>
                <th className="py-1 pr-3 font-medium">Source id</th>
                <th className="py-1 pr-3 font-medium">Problem</th>
                <th className="py-1 pr-3 font-medium">Error</th>
                <th className="py-1 pr-3 text-right font-medium">Tries</th>
                <th className="py-1 pr-3 font-medium">When</th>
                <th className="py-1 text-right font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f) => (
                <FailureRow key={f.id} f={f} runId={runId} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {q.hasNextPage && (
        <Button
          variant="outline"
          size="sm"
          loading={q.isFetchingNextPage}
          onClick={() => void q.fetchNextPage()}
        >
          Load more
        </Button>
      )}
    </section>
  );
}

/** Replace the stored key of a connection after the source refused it mid-import. */
type CredField = { key: string; label: string; optional?: boolean };
export type CredMode = { id: string; label: string; credentials: CredField[]; help?: string };

export function UpdateCredentials({
  connectionId,
  fields: singleFields,
  modes,
  onUpdated,
}: {
  connectionId: string;
  fields: CredField[];
  /** Sources with more than one way to sign in (Zendesk) offer the same choice as the connect form. */
  modes?: CredMode[];
  onUpdated: () => void;
}) {
  const [modeId, setModeId] = useState(modes?.[0]?.id ?? "");
  const mode = modes?.find((m) => m.id === modeId);
  const fields = mode?.credentials ?? singleFields;
  const [open, setOpen] = useState(false);
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const update = useUpdateImportCredentials(connectionId);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-sm font-medium text-primary-ink hover:underline"
      >
        Key changed or revoked? Update credentials
      </button>
    );
  }
  const missing = fields.some((f) => !f.optional && !creds[f.key]?.trim());
  return (
    <form
      className="space-y-3 rounded-xl border border-border p-4"
      noValidate
      autoComplete="off"
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        const credentials = Object.fromEntries(
          Object.entries(creds)
            .filter(([, v]) => v.trim())
            .map(([k, v]) => [k, v.trim()]),
        );
        update.mutate(credentials, {
          onSuccess: () => {
            toast.success("Credentials updated");
            setCreds({});
            setOpen(false);
            onUpdated();
          },
          onError: (err) =>
            setError(
              err instanceof ApiError
                ? err.status === 503
                  ? "The source can't be reached right now. Try again in a moment."
                  : conflictMessage(err)
                : "Couldn't update the credentials.",
            ),
        });
      }}
    >
      {modes && modes.length > 1 && (
        <fieldset>
          <legend className="text-[13px] font-medium text-foreground-2">How to connect</legend>
          <div className="mt-1.5 flex flex-wrap gap-3">
            {modes.map((m) => (
              <label key={m.id} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="upd-auth-mode"
                  checked={modeId === m.id}
                  onChange={() => {
                    setModeId(m.id);
                    setCreds({});
                  }}
                />
                {m.label}
              </label>
            ))}
          </div>
          {mode?.help && <p className="mt-1.5 text-xs text-muted-foreground">{mode.help}</p>}
        </fieldset>
      )}
      {fields.map((f) => (
        <Field key={f.key} label={f.label} htmlFor={`upd-${f.key}`}>
          <PasswordInput
            id={`upd-${f.key}`}
            autoComplete="off"
            value={creds[f.key] ?? ""}
            onChange={(e) => setCreds((s) => ({ ...s, [f.key]: e.target.value }))}
          />
        </Field>
      ))}
      <FormError message={error ?? undefined} />
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={missing} loading={update.isPending}>
          Update credentials
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

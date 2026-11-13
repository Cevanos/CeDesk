"use client";

import { Download, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { Badge, Button, Card, EmptyState, Select, Skeleton } from "@/components/ui/primitives";
import { LoadError } from "@/components/workspace/list-shared";
import {
  auditExportUrl,
  useAuditLog,
  useVerifyAudit,
  type AuditEntry,
} from "@/hooks/use-workspace";

const AREAS = [
  "",
  "security",
  "members",
  "teams",
  "tickets",
  "rules",
  "email",
  "apps",
  "api_keys",
  "webhooks",
  "privacy",
  "retention",
  "billing",
];

const label = (a: string) => (a ? a.replace(/_/g, " ") : "All areas");

function actorOf(e: AuditEntry): string {
  if (e.actor_name) return e.actor_name;
  if (typeof e.actor === "string") return e.actor;
  return e.actor?.name ?? e.actor?.email ?? "System";
}

export function AuditView() {
  const [area, setArea] = useState("");
  const log = useAuditLog(area);
  const verify = useVerifyAudit();
  const rows = log.data?.pages.flatMap((p) => p.items) ?? [];
  const v = verify.data;
  const verified = v ? (v.ok ?? v.valid) : undefined;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="w-52">
          <label htmlFor="audit-area" className="mb-1.5 block text-[13px] font-medium">
            Area
          </label>
          <Select
            id="audit-area"
            value={area}
            options={AREAS.map((a) => ({ value: a, label: label(a) }))}
            onChange={(e) => setArea(e.target.value)}
          />
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          <Button variant="outline" loading={verify.isPending} onClick={() => verify.mutate()}>
            <ShieldCheck className="size-4" /> Verify chain
          </Button>
          <a
            href={auditExportUrl(area)}
            download
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-border-strong bg-surface px-4 text-sm hover:bg-surface-2"
          >
            <Download className="size-4" /> Download CSV
          </a>
        </div>
      </div>

      {verify.isError && <LoadError error={verify.error} />}
      {v && (
        <p
          role="status"
          className={`mb-4 rounded-lg border px-3 py-2 text-sm ${
            verified === false
              ? "border-danger/25 bg-danger-soft text-danger"
              : "border-success/30 bg-success-soft text-success"
          }`}
        >
          {v.message ??
            (verified === false
              ? `The log has been tampered with${v.broken_at != null ? ` (at entry ${v.broken_at})` : ""}.`
              : `The log is intact${typeof v.checked === "number" ? ` (${v.checked} entries checked)` : ""}.`)}
        </p>
      )}

      {log.isPending ? (
        <Skeleton className="h-40" />
      ) : log.isError ? (
        <LoadError error={log.error} />
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState title="Nothing logged here yet">
            Changes to settings, members and security show up in this list.
          </EmptyState>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-border">
            {rows.map((e, i) => (
              <li key={e.id ?? i} className="flex flex-wrap items-start gap-3 px-4 py-3 text-sm">
                <div className="min-w-0 flex-1 basis-60">
                  <p>{e.summary ?? e.action ?? "Change"}</p>
                  <p className="text-xs text-muted-foreground">
                    {actorOf(e)}
                    {e.target ? ` · ${e.target}` : ""}
                  </p>
                </div>
                {e.area && <Badge>{label(e.area)}</Badge>}
                <time className="text-xs text-muted-foreground">
                  {new Date(e.at ?? e.created_at ?? "").toLocaleString()}
                </time>
              </li>
            ))}
          </ul>
          {log.hasNextPage && (
            <div className="border-t border-border p-3 text-center">
              <Button
                variant="ghost"
                loading={log.isFetchingNextPage}
                onClick={() => log.fetchNextPage()}
              >
                Load more
              </Button>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

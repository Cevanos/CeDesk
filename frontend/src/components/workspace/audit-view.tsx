"use client";

import { ChevronDown, ChevronRight, Download, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Card, EmptyState, Select, Skeleton } from "@/components/ui/primitives";
import { LoadError } from "@/components/workspace/list-shared";
import { useAuditLog, useVerifyAudit, type AuditEntry } from "@/hooks/use-workspace";
import { ApiError, downloadFile } from "@/lib/api/client";

const label = (a: string) => (a ? a.replace(/_/g, " ") : "All areas");
const show = (v: unknown) => (v === null || v === undefined || v === "" ? "(empty)" : String(v));

function Row({ e }: { e: AuditEntry }) {
  const [open, setOpen] = useState(false);
  const changes = Object.entries(e.detail?.changes ?? {});
  const canOpen = changes.length > 0;
  return (
    <li className="text-sm">
      <button
        type="button"
        disabled={!canOpen}
        aria-expanded={canOpen ? open : undefined}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full flex-wrap items-start gap-3 px-4 py-3 text-left enabled:hover:bg-surface-2"
      >
        <span className="mt-0.5 w-4 flex-none text-muted-foreground" aria-hidden>
          {canOpen &&
            (open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />)}
        </span>
        <div className="min-w-0 flex-1 basis-60">
          <p>
            {e.action}
            {e.target ? ` · ${e.target}` : ""}
          </p>
          <p className="text-xs text-muted-foreground">
            {e.actor_name ?? "System"}
            {e.ip ? ` · ${e.ip}` : ""}
          </p>
        </div>
        <Badge>{label(e.area)}</Badge>
        <time className="text-xs text-muted-foreground" dateTime={e.at}>
          {new Date(e.at).toLocaleString()}
        </time>
      </button>
      {open && canOpen && (
        <dl className="mx-4 mb-3 ml-11 space-y-1 rounded-lg bg-surface-2 px-3 py-2 text-xs">
          {changes.map(([field, [before, after]]) => (
            <div key={field} className="flex flex-wrap gap-x-2">
              <dt className="font-medium">{field.replace(/_/g, " ")}</dt>
              <dd className="text-muted-foreground">
                {show(before)} <span aria-label="changed to">→</span> {show(after)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

export function AuditView() {
  const [area, setArea] = useState("");
  const [csvBusy, setCsvBusy] = useState(false);
  const log = useAuditLog(area);
  const verify = useVerifyAudit();
  const rows = log.data?.pages.flatMap((p) => p.entries) ?? [];
  // The area list comes with every page; the first one is enough.
  const areas = log.data?.pages[0]?.areas ?? [];
  const v = verify.data;

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
            options={["", ...areas].map((a) => ({ value: a, label: label(a) }))}
            onChange={(e) => setArea(e.target.value)}
          />
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          <Button variant="outline" loading={verify.isPending} onClick={() => verify.mutate()}>
            <ShieldCheck className="size-4" /> Verify chain
          </Button>
          <Button
            variant="outline"
            loading={csvBusy}
            onClick={() => {
              setCsvBusy(true);
              downloadFile("audit/export.csv", "audit-log.csv", { area })
                .catch((e) =>
                  toast.error(e instanceof ApiError ? e.userMessage : "Download failed"),
                )
                .finally(() => setCsvBusy(false));
            }}
          >
            <Download className="size-4" /> Download CSV
          </Button>
        </div>
      </div>

      {verify.isError && (
        <div className="mb-4">
          <LoadError error={verify.error} />
        </div>
      )}
      {v && (
        <p
          role="status"
          className={`mb-4 rounded-lg border px-3 py-2 text-sm ${
            v.ok
              ? "border-success/30 bg-success-soft text-success"
              : "border-danger/25 bg-danger-soft text-danger"
          }`}
        >
          {v.ok
            ? `Chain is intact. ${v.checked} entries checked.`
            : (v.message ?? "The chain is broken.")}
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
              <Row key={e.id ?? `${e.at}-${i}`} e={e} />
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

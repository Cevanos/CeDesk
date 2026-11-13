"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Card, Field, Input, Skeleton } from "@/components/ui/primitives";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import {
  parseDays,
  useArchiveSettings,
  useRetention,
  useSaveArchiveSettings,
  useSaveRetention,
  useUsage,
  usageRows,
} from "@/hooks/use-workspace";

export function PlanView() {
  return (
    <div className="space-y-6">
      <UsageCard />
      <RetentionCard />
      <ArchiveCard />
    </div>
  );
}

function UsageCard() {
  const q = useUsage();
  if (q.isPending) return <Skeleton className="h-32" />;
  if (q.isError) return <LoadError error={q.error} />;
  const rows = usageRows(q.data);
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-[15px] font-semibold">Usage</h2>
        {q.data?.plan && <Badge tone="primary">{String(q.data.plan)}</Badge>}
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No usage limits apply to this workspace.</p>
      ) : (
        <ul className="space-y-3">
          {rows.map((r, i) => {
            const used = r.used ?? 0;
            const pct = r.limit ? Math.min(100, Math.round((used / r.limit) * 100)) : 0;
            return (
              <li key={r.key ?? i}>
                <div className="flex justify-between text-sm">
                  <span className="capitalize">{r.label ?? r.key}</span>
                  <span className="text-muted-foreground tabular-nums">
                    {used}
                    {r.limit ? ` / ${r.limit}` : " (no limit)"}
                  </span>
                </div>
                {r.limit ? (
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-3">
                    <div
                      className={`h-full ${pct >= 90 ? "bg-danger" : "bg-primary"}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function RetentionCard() {
  const q = useRetention();
  if (q.isPending) return <Skeleton className="h-32" />;
  if (q.isError) return <LoadError error={q.error} />;
  return <RetentionForm initial={q.data ?? {}} />;
}

function RetentionForm({ initial }: { initial: Record<string, unknown> }) {
  const save = useSaveRetention();
  const [days, setDays] = useState(
    initial.resolved_ticket_days ? String(initial.resolved_ticket_days) : "",
  );
  const [errs, setErrs] = useState<Record<string, string>>({});
  return (
    <Card className="p-5">
      <h2 className="text-[15px] font-semibold">Retention</h2>
      <form
        noValidate
        className="mt-3 space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          const n = days.trim() ? parseDays(days) : 0;
          if (n === null) return setErrs({ resolved_ticket_days: "Enter a number of days" });
          setErrs({});
          save.mutate(
            { ...initial, resolved_ticket_days: n || null },
            {
              onSuccess: () => toast.success("Retention saved"),
              onError: (err) => setErrs(fieldErrors(err)),
            },
          );
        }}
      >
        <Field
          label="Delete resolved tickets after (days)"
          htmlFor="ret-days"
          error={errs.resolved_ticket_days}
          hint="Leave empty to keep resolved tickets forever. Deleted tickets can't be recovered."
        >
          <Input
            id="ret-days"
            inputMode="numeric"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </Field>
        <FormError message={errs._form} />
        <div className="flex justify-end">
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
  if (q.isPending) return <Skeleton className="h-32" />;
  if (q.isError) return <LoadError error={q.error} />;
  return <ArchiveForm initial={q.data ?? {}} />;
}

function ArchiveForm({ initial }: { initial: Record<string, unknown> }) {
  const save = useSaveArchiveSettings();
  const [days, setDays] = useState(String(initial.archive_age_days ?? 120));
  const [errs, setErrs] = useState<Record<string, string>>({});
  return (
    <Card className="p-5">
      <h2 className="text-[15px] font-semibold">Archive</h2>
      <form
        noValidate
        className="mt-3 space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          const n = parseDays(days);
          if (n === null || n < 1) return setErrs({ archive_age_days: "Enter a number of days" });
          setErrs({});
          save.mutate(
            { ...initial, archive_age_days: n },
            {
              onSuccess: () => toast.success("Archive age saved"),
              onError: (err) => setErrs(fieldErrors(err)),
            },
          );
        }}
      >
        <Field
          label="Archive closed tickets after (days)"
          htmlFor="arc-days"
          error={errs.archive_age_days}
          hint="Default is 120 days."
        >
          <Input
            id="arc-days"
            inputMode="numeric"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </Field>
        <FormError message={errs._form} />
        <div className="flex justify-end">
          <Button type="submit" loading={save.isPending}>
            Save
          </Button>
        </div>
      </form>
    </Card>
  );
}

"use client";

import { useState } from "react";
import { toast } from "sonner";

import { PRIORITY_LABEL } from "@/components/tickets/badges";
import { formatMinutes } from "@/components/tickets/sla";
import { Badge, Button, Card, Input, Skeleton } from "@/components/ui/primitives";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import { SlaHolidays } from "@/components/workspace/sla-holidays";
import { SlaHoursEditor } from "@/components/workspace/sla-hours-editor";
import { SlaPoliciesEditor } from "@/components/workspace/sla-policies-editor";
import { useCurrentUser } from "@/hooks/use-auth";
import { useSaveSlaTargets, useSla } from "@/hooks/use-workspace";
import type { SlaSettings, SlaTarget } from "@/lib/api/types";

export function SlaView() {
  const sla = useSla();
  if (sla.isPending) return <Skeleton className="h-48" />;
  if (sla.isError) return <LoadError error={sla.error} />;
  return <SlaBody data={sla.data} />;
}

function SlaBody({ data }: { data: SlaSettings }) {
  const { perms } = useCurrentUser();
  const save = useSaveSlaTargets();
  const [rows, setRows] = useState(() =>
    data.targets.map((t) => ({
      priority: t.priority,
      first: String(t.first_reply_minutes),
      resolve: String(t.resolve_minutes),
    })),
  );
  const [errs, setErrs] = useState<Record<string, string>>({});
  const canEdit = perms.manageSettings;
  const cal = data.calendar;
  // A fresh copy of the server data resets the editors' drafts (after a save or reset).
  const policiesKey = JSON.stringify(data.policies);
  const hoursKey = JSON.stringify({ ...cal, holidays: undefined });

  const submit = () => {
    const next: Record<string, string> = {};
    const targets = rows.map((r) => {
      const first = Number(r.first);
      const resolve = Number(r.resolve);
      if (!Number.isInteger(first) || first < 1)
        next[`${r.priority}-first`] = "Whole minutes, 1 or more";
      if (!Number.isInteger(resolve) || resolve < 1)
        next[`${r.priority}-resolve`] = "Whole minutes, 1 or more";
      if (!next[`${r.priority}-first`] && !next[`${r.priority}-resolve`] && first > resolve)
        next[`${r.priority}-first`] = "First reply can't be later than resolution";
      return { priority: r.priority, first_reply_minutes: first, resolve_minutes: resolve };
    });
    setErrs(next);
    if (Object.keys(next).length) return;
    save.mutate(targets, {
      onSuccess: () => toast.success("SLA targets saved"),
      onError: (e) => setErrs(fieldErrors(e)),
    });
  };

  return (
    <div className="space-y-6">
      <section aria-labelledby="sla-targets">
        <h2 id="sla-targets" className="text-base font-semibold">
          Targets by priority
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Time to first reply and to resolution, counted in business minutes.
          {!canEdit && " Only an Admin can change them."}
        </p>
        <Card className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[34rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th scope="col" className="px-4 py-2.5 font-medium">
                  Priority
                </th>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  First reply (minutes)
                </th>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  Resolution (minutes)
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((r, i) => {
                const t: SlaTarget | undefined = data.targets[i];
                return (
                  <tr key={r.priority}>
                    <th scope="row" className="px-4 py-3 text-left font-medium">
                      {PRIORITY_LABEL[r.priority]}
                      {t?.is_default && <Badge className="ml-2">Default</Badge>}
                    </th>
                    {(["first", "resolve"] as const).map((k) => {
                      const key = `${r.priority}-${k}`;
                      const n = Number(r[k]);
                      return (
                        <td key={k} className="px-4 py-2 align-top">
                          <Input
                            aria-label={`${PRIORITY_LABEL[r.priority]} ${k === "first" ? "first reply" : "resolution"} minutes`}
                            type="number"
                            min={1}
                            inputMode="numeric"
                            className="w-32"
                            value={r[k]}
                            disabled={!canEdit}
                            aria-invalid={!!errs[key]}
                            onChange={(e) =>
                              setRows((rs) =>
                                rs.map((x, n2) => (n2 === i ? { ...x, [k]: e.target.value } : x)),
                              )
                            }
                          />
                          {errs[key] ? (
                            <p role="alert" className="mt-1 text-xs text-danger">
                              {errs[key]}
                            </p>
                          ) : (
                            Number.isFinite(n) &&
                            n > 0 && (
                              <p className="mt-1 text-xs text-muted-foreground">
                                {formatMinutes(n)}
                              </p>
                            )
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
        {errs._form && (
          <div className="mt-3">
            <FormError message={errs._form} />
          </div>
        )}
        {canEdit && (
          <div className="mt-3 flex justify-end">
            <Button onClick={submit} loading={save.isPending}>
              Save targets
            </Button>
          </div>
        )}
      </section>

      <SlaPoliciesEditor
        key={policiesKey}
        policies={data.policies}
        isDefault={data.policies_are_default}
        canEdit={canEdit}
      />

      <section aria-labelledby="sla-hours">
        <h2 id="sla-hours" className="text-base font-semibold">
          Business hours and holidays
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          The timer pauses outside these hours and on holidays.
          {!canEdit && " Only an Admin can change them."}
        </p>
        <Card className="mt-3 space-y-5 p-4 text-sm">
          <SlaHoursEditor key={hoursKey} calendar={cal} canEdit={canEdit} />
          <div className="border-t border-border pt-4">
            <SlaHolidays holidays={cal.holidays} canEdit={canEdit} />
          </div>
        </Card>
      </section>
    </div>
  );
}

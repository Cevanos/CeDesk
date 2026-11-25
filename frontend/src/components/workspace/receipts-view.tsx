"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Button, Card, Skeleton } from "@/components/ui/primitives";
import { FormError, LoadError } from "@/components/workspace/list-shared";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import { useReceiptSettings, useSaveReceiptSettings, useTeams } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { ReceiptMode, ReceiptSettings } from "@/lib/api/differentiator-types";
import { formatDateTime } from "@/lib/utils";

const MODES: { value: ReceiptMode; title: string; body: string }[] = [
  {
    value: "off",
    title: "Off",
    body: "Resolving works as it does today. No receipt is asked for.",
  },
  {
    value: "optional",
    title: "Optional",
    body: "Agents are asked what was done when they resolve, and can skip it.",
  },
  {
    value: "required",
    title: "Required",
    body: "Agents must record what was done. It applies to resolving by status, bulk change and macros too.",
  },
];

export function ReceiptsView() {
  const q = useReceiptSettings();
  if (q.isPending) return <Skeleton className="h-64" />;
  if (q.isError) return <LoadError error={q.error} />;
  return <Body key={JSON.stringify(q.data)} saved={q.data} />;
}

function Body({ saved }: { saved: ReceiptSettings }) {
  const save = useSaveReceiptSettings();
  const teams = useTeams();
  const [mode, setMode] = useState<ReceiptMode>(saved.mode);
  const [req, setReq] = useState<string[]>(saved.required_teams);
  const [error, setError] = useState<string>();

  const dirty =
    mode !== saved.mode ||
    JSON.stringify([...req].sort()) !== JSON.stringify([...saved.required_teams].sort());
  useUnsavedChanges(dirty);

  const toggle = (id: string) =>
    setReq((r) => (r.includes(id) ? r.filter((x) => x !== id) : [...r, id]));

  return (
    <div className="space-y-6">
      <div>
        <h2 className="hidden text-lg font-semibold tracking-tight lg:block">
          Resolution receipts
        </h2>
        <p className="text-sm text-muted-foreground">
          When an agent resolves a ticket, they record what was done and whether they checked it.
          Customers see the outcome in the portal once it is verified.
        </p>
      </div>

      <fieldset>
        <legend className="text-base font-semibold">Mode</legend>
        <Card className="mt-3 divide-y divide-border">
          {MODES.map((m) => (
            <div key={m.value} className="flex items-start gap-3 p-4">
              <input
                id={`rm-${m.value}`}
                type="radio"
                name="receipt-mode"
                aria-describedby={`rm-${m.value}-d`}
                className="mt-1 size-4 accent-[var(--primary)]"
                checked={mode === m.value}
                onChange={() => {
                  setMode(m.value);
                  setError(undefined);
                }}
              />
              <div>
                <label
                  htmlFor={`rm-${m.value}`}
                  className="block cursor-pointer text-sm font-medium"
                >
                  {m.title}
                </label>
                <p id={`rm-${m.value}-d`} className="mt-0.5 text-sm text-muted-foreground">
                  {m.body}
                </p>
              </div>
            </div>
          ))}
        </Card>
      </fieldset>

      {mode === "required" && (
        <fieldset>
          <legend className="text-base font-semibold">Required for</legend>
          <p className="mt-1 text-sm text-muted-foreground">
            Pick the teams that must write a receipt. Leave all unticked to require it for every
            team.
          </p>
          <Card className="mt-3 divide-y divide-border">
            {teams.isPending ? (
              <Skeleton className="h-16" />
            ) : (teams.data ?? []).length === 0 ? (
              <p className="p-4 text-sm text-muted-foreground">There are no teams yet.</p>
            ) : (
              (teams.data ?? []).map((t) => (
                <label
                  key={t.id}
                  className="flex cursor-pointer items-center gap-3 px-4 py-3 text-sm"
                >
                  <input
                    type="checkbox"
                    className="size-4 accent-[var(--primary)]"
                    checked={req.includes(t.id)}
                    onChange={() => toggle(t.id)}
                  />
                  {t.name}
                </label>
              ))
            )}
          </Card>
          <p className="mt-2 text-xs text-muted-foreground">
            {req.length === 0
              ? "Every team."
              : `${req.length} team${req.length === 1 ? "" : "s"} selected.`}
          </p>
        </fieldset>
      )}

      <FormError message={error} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {saved.updated_at
            ? `Last changed${saved.updated_by_name ? ` by ${saved.updated_by_name}` : ""}, ${formatDateTime(saved.updated_at)}`
            : ""}
        </p>
        <div className="flex gap-3">
          <Button
            variant="ghost"
            disabled={!dirty || save.isPending}
            onClick={() => {
              setMode(saved.mode);
              setReq(saved.required_teams);
              setError(undefined);
            }}
          >
            Discard
          </Button>
          <Button
            disabled={!dirty}
            loading={save.isPending}
            onClick={() =>
              save.mutate(
                { mode, required_teams: mode === "required" ? req : [] },
                {
                  onSuccess: () => toast.success("Resolution receipts saved"),
                  onError: (e) =>
                    setError(e instanceof ApiError ? e.userMessage : "Couldn't save. Try again."),
                },
              )
            }
          >
            Save changes
          </Button>
        </div>
      </div>
    </div>
  );
}

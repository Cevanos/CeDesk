"use client";

import { Rocket } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Dialog } from "@/components/ui/overlay";
import { Badge, Button, Skeleton } from "@/components/ui/primitives";
import { FormError, LoadError } from "@/components/workspace/list-shared";
import { msg422 } from "@/components/workspace/sandbox-errors";
import {
  useDeploySandbox,
  useSandboxCompare,
  useTrySandbox,
  type CompareSection,
  type TrySide,
} from "@/hooks/use-workspace";
import { cn } from "@/lib/utils";

export function sideText(s: TrySide): string {
  const parts = [
    s.type && `Type ${s.type}`,
    s.team && `Team ${s.team}`,
    s.priority && `Priority ${s.priority}`,
    s.status && `Status ${s.status}`,
    s.tags?.length ? `Tags ${s.tags.join(", ")}` : "",
  ].filter(Boolean);
  return parts.join(" · ") || "No changes";
}

export function SideCell({ title, s }: { title: string; s: TrySide }) {
  return (
    <div>
      <p className="text-xs font-semibold text-muted-foreground">{title}</p>
      <p className="text-sm">{sideText(s)}</p>
      {s.lines?.map((l, i) => (
        <p key={i} className="text-xs text-muted-foreground">
          {l}
        </p>
      ))}
    </div>
  );
}

export function TryDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const run = useTrySandbox();
  const [error, setError] = useState<string | null>(null);
  const rows = run.data ?? [];
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Try on samples"
      description="Runs your live rules and your sandbox rules on sample tickets and shows the difference."
      className="max-w-3xl"
    >
      <div className="space-y-3">
        <Button
          loading={run.isPending}
          onClick={() => {
            setError(null);
            run.mutate(undefined, { onError: (e) => setError(msg422(e)) });
          }}
        >
          {run.data ? "Run again" : "Run the samples"}
        </Button>
        <FormError message={error ?? undefined} />
        {run.data && rows.length === 0 && (
          <p className="text-sm text-muted-foreground">There are no samples to run.</p>
        )}
        <ul className="space-y-2">
          {rows.map((r, i) => (
            <li
              key={`${r.sample}-${i}`}
              className={cn(
                "rounded-lg border p-3",
                r.same ? "border-border" : "border-warning/40 bg-warning-soft",
              )}
            >
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">{r.sample}</span>
                {r.channel && <Badge>{r.channel}</Badge>}
                <Badge tone={r.same ? "success" : "warning"}>{r.same ? "Same" : "Different"}</Badge>
              </div>
              {r.text && <p className="mb-2 text-xs text-muted-foreground">{r.text}</p>}
              <div className="grid gap-3 sm:grid-cols-2">
                <SideCell title="Live" s={r.live} />
                <SideCell title="Sandbox" s={r.sandbox} />
              </div>
            </li>
          ))}
        </ul>
      </div>
    </Dialog>
  );
}

/* ---------- compare and deploy ---------- */

export const itemName = (x: unknown): string => {
  if (typeof x === "string") return x;
  if (x && typeof x === "object") {
    const o = x as Record<string, unknown>;
    for (const k of ["label", "name", "shortcut"]) if (typeof o[k] === "string") return o[k];
  }
  return "Item";
};
export const changeText = (c: unknown): string => {
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.map(changeText).join(", ");
  if (c && typeof c === "object") return Object.keys(c).join(", ");
  return "";
};

export function SectionDiff({ title, s }: { title: string; s: CompareSection }) {
  const none =
    s.added.length === 0 && s.changed.length === 0 && s.removed.length === 0 && !s.order_changed;
  return (
    <section>
      <h3 className="text-sm font-semibold">{title}</h3>
      {none ? (
        <p className="text-sm text-muted-foreground">No differences.</p>
      ) : (
        <ul className="mt-1 space-y-1 text-sm">
          {s.added.map((x, i) => (
            <li key={`a${i}`}>
              <Badge tone="success">Added</Badge> {itemName(x)}
            </li>
          ))}
          {s.changed.map((x, i) => (
            <li key={`c${i}`}>
              <Badge tone="warning">Changed</Badge> {x.label ?? x.id}
              {changeText(x.changes) && (
                <span className="text-muted-foreground"> ({changeText(x.changes)})</span>
              )}
            </li>
          ))}
          {s.removed.map((x, i) => (
            <li key={`r${i}`}>
              <Badge tone="danger">Removed</Badge> {itemName(x)}
            </li>
          ))}
          {s.order_changed && (
            <li>
              <Badge>Order</Badge> The order is different.
            </li>
          )}
        </ul>
      )}
    </section>
  );
}

export function CompareDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const q = useSandboxCompare(open);
  const deploy = useDeploySandbox();
  const [error, setError] = useState<string | null>(null);
  const c = q.data;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Compare with live"
      description="What deploying would change on your live workspace."
      className="max-w-2xl"
    >
      {q.isPending ? (
        <Skeleton className="h-32" />
      ) : q.isError ? (
        <LoadError error={q.error} />
      ) : (
        c && (
          <div className="space-y-4">
            {c.same && (
              <p role="status" className="rounded-lg bg-surface-2 px-3 py-2 text-sm">
                The sandbox is the same as live. There is nothing to deploy.
              </p>
            )}
            <SectionDiff title="Rules" s={c.rules} />
            <SectionDiff title="Macros" s={c.macros} />
            <SectionDiff title="Saved replies" s={c.saved_replies} />
            <FormError message={error ?? undefined} />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={onClose}>
                Close
              </Button>
              <Button
                disabled={c.same}
                loading={deploy.isPending}
                onClick={() => {
                  setError(null);
                  deploy.mutate(undefined, {
                    onSuccess: () => {
                      toast.success("Deployed to live");
                      onClose();
                    },
                    onError: (e) => setError(msg422(e)),
                  });
                }}
              >
                <Rocket className="size-4" /> Deploy to live
              </Button>
            </div>
          </div>
        )
      )}
    </Dialog>
  );
}

"use client";

import { FlaskConical } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button, Card } from "@/components/ui/primitives";
import { useLoadSamples, useResetDemo, useSeedDemo } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";

export function DemoView() {
  const seed = useSeedDemo();
  const samples = useLoadSamples();
  const reset = useResetDemo();
  // 403 from the API means demo is off on this server (or this is production).
  const [off, setOff] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const busy = seed.isPending || samples.isPending || reset.isPending;

  const run =
    (m: typeof seed, done: (r: { created?: number; deleted?: number } | null) => string) => () => {
      setError(null);
      m.mutate(undefined, {
        onSuccess: (r) => toast.success(done(r)),
        onError: (e) => {
          const msg = e instanceof ApiError ? e.userMessage : "Something went wrong.";
          if (e instanceof ApiError && e.status === 403) setOff(msg);
          else setError(msg);
        },
      });
    };

  return (
    <Card className="p-5">
      <div className="flex items-start gap-3">
        <span className="grid size-10 flex-none place-items-center rounded-xl bg-primary-soft text-primary-ink">
          <FlaskConical className="size-5" />
        </span>
        <div>
          <h2 className="text-[15px] font-semibold">Demo data</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Fill this workspace with example tickets so you can try CDesk. They are marked
            &ldquo;Demo&rdquo;. Only available when demo mode is on for this server.
          </p>
        </div>
      </div>

      {off ? (
        <p role="alert" className="mt-5 rounded-lg bg-surface-2 px-3.5 py-3 text-sm">
          {off}
        </p>
      ) : (
        <div className="mt-5 flex flex-wrap gap-2">
          <Button
            loading={seed.isPending}
            disabled={busy}
            onClick={run(seed, (r) => `Loaded ${r?.created ?? 16} demo tickets`)}
          >
            Load demo data (16 tickets)
          </Button>
          <Button
            variant="outline"
            loading={samples.isPending}
            disabled={busy}
            onClick={run(samples, () => "Loaded 6 sample tickets")}
          >
            Load sample tickets (6)
          </Button>
          <Button
            variant="outline"
            loading={reset.isPending}
            disabled={busy}
            onClick={() => {
              if (!confirm("Reset demo data? Only demo tickets are removed.")) return;
              run(
                reset,
                (r) => `Demo data reset${r?.deleted != null ? ` (${r.deleted} removed)` : ""}`,
              )();
            }}
          >
            Reset demo
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
      <p className="mt-4 text-xs text-muted-foreground">
        Reset removes demo tickets only. Real and imported tickets stay.
      </p>
    </Card>
  );
}

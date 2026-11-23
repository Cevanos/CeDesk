"use client";

import { Download } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/primitives";
import { retryOnce, runBody, runPath, type RunSource } from "@/hooks/workspace/reports-explore";
import { ApiError, downloadFile, downloadFilePost, type Query } from "@/lib/api/client";
import { csvName } from "@/lib/reports";

/** Runs a download; a 429 is retried once after `Retry-After`, any error shows its message. */
export function useCsvDownload() {
  const [busy, setBusy] = useState(false);
  const run = (fn: () => Promise<void>) => {
    setBusy(true);
    retryOnce(fn)
      .catch((e) => toast.error(e instanceof ApiError ? e.userMessage : "Download failed"))
      .finally(() => setBusy(false));
  };
  return { busy, run };
}

function Btn({
  busy,
  onClick,
  label = "Download CSV",
  disabled,
}: {
  busy: boolean;
  onClick: () => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <Button size="sm" variant="outline" loading={busy} disabled={disabled} onClick={onClick}>
      {!busy && <Download className="size-3.5" aria-hidden />} {label}
    </Button>
  );
}

/** CSV of a builder, built-in or saved report: the same call as the run, with `format=csv`. */
export function RunCsvButton({
  source,
  metric,
  groupBy,
  disabled,
}: {
  source: RunSource;
  metric: string;
  groupBy?: string;
  disabled?: boolean;
}) {
  const { busy, run } = useCsvDownload();
  return (
    <Btn
      busy={busy}
      disabled={disabled}
      onClick={() =>
        run(() =>
          downloadFilePost(runPath(source), runBody(source), csvName(metric, groupBy), {
            format: "csv",
          }),
        )
      }
    />
  );
}

/** CSV of any POST route (a dashboard card). */
export function PostCsvButton({
  path,
  body,
  fallback,
  label,
}: {
  path: string;
  body: unknown;
  fallback: string;
  label?: string;
}) {
  const { busy, run } = useCsvDownload();
  return (
    <Btn
      busy={busy}
      label={label}
      onClick={() => run(() => downloadFilePost(path, body, fallback, { format: "csv" }))}
    />
  );
}

/** CSV of a GET route (backlog, live). */
export function GetCsvButton({
  path,
  query,
  fallback,
}: {
  path: string;
  query: Query;
  fallback: string;
}) {
  const { busy, run } = useCsvDownload();
  return (
    <Btn
      busy={busy}
      onClick={() => run(() => downloadFile(path, fallback, { ...query, format: "csv" }))}
    />
  );
}

"use client";

import { Download, Loader2, Paperclip, ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { downloadAttachment } from "@/hooks/use-tickets";
import { ApiError } from "@/lib/api/client";
import type { ScanStatus } from "@/lib/api/types";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

export { formatBytes };

/** `infected` and `scan_failed` are blocked; everything else may be downloaded. */
export const isBlocked = (s?: ScanStatus) => s === "infected" || s === "scan_failed";

/** One file: download link, or a red "Blocked" chip. A 409 on download means "still checking". */
export function FileChip({
  file,
  className,
  portal,
}: {
  file: { id: string; name: string; size?: number; scan_status?: ScanStatus };
  className?: string;
  /** A customer on the portal: uses the portal file route and the customer's own cookie. */
  portal?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const blocked = isBlocked(file.scan_status);

  if (file.scan_status === "erased") {
    return (
      <span
        className={cn(
          "inline-flex max-w-full items-center gap-1.5 rounded-lg border border-border bg-surface-2 px-2.5 py-1.5 text-xs text-muted-foreground",
          className,
        )}
        title="This file was erased at the customer's request."
      >
        <Paperclip className="size-3.5 flex-none" aria-hidden />
        <span className="min-w-0 truncate line-through">{file.name}</span>
        <span className="font-semibold">Removed</span>
      </span>
    );
  }

  if (blocked) {
    return (
      <span
        className={cn(
          "inline-flex max-w-full items-center gap-1.5 rounded-lg border border-danger/30 bg-danger-soft px-2.5 py-1.5 text-xs text-danger",
          className,
        )}
        title={
          file.scan_status === "infected"
            ? "This file may contain a virus and can't be downloaded."
            : "This file could not be checked for viruses and can't be downloaded."
        }
      >
        <ShieldAlert className="size-3.5 flex-none" aria-hidden />
        <span className="min-w-0 truncate line-through">{file.name}</span>
        <span className="font-semibold">Blocked</span>
      </span>
    );
  }

  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => {
        setBusy(true);
        downloadAttachment(file.id, portal ? "portal/attachments" : "attachments", file.name)
          .catch((e) => {
            if (e instanceof ApiError && e.status === 409) {
              toast.info(e.message || "Checking this file for viruses… try again in a moment.");
            } else {
              toast.error(e instanceof ApiError ? e.userMessage : "Download failed");
            }
          })
          .finally(() => setBusy(false));
      }}
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs font-medium text-foreground-2 transition-colors hover:bg-surface-2 disabled:opacity-60",
        className,
      )}
    >
      {busy ? (
        <Loader2 className="size-3.5 flex-none animate-spin" aria-hidden />
      ) : (
        <Paperclip className="size-3.5 flex-none text-muted-foreground" aria-hidden />
      )}
      <span className="min-w-0 truncate">{file.name}</span>
      {file.size !== undefined && (
        <span className="flex-none text-muted-foreground">{formatBytes(file.size)}</span>
      )}
      {file.scan_status === "not_scanned" ? (
        <span
          className="inline-flex flex-none items-center gap-1 text-muted-foreground"
          title="Being checked for viruses. If it isn't ready yet, try again in a moment."
        >
          <ShieldCheck className="size-3" aria-hidden />
          Checking…
        </span>
      ) : (
        <Download className="size-3 flex-none text-muted-foreground" aria-hidden />
      )}
    </button>
  );
}

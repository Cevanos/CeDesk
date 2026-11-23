"use client";

import { TriangleAlert } from "lucide-react";

import { CopyButton } from "@/components/ui/copy-button";
import { Dialog } from "@/components/ui/overlay";
import { Button } from "@/components/ui/primitives";

/** Shows a secret (API key, webhook secret) that the server will never show again. */
export function SecretDialog({
  title,
  secret,
  noun,
  onClose,
}: {
  title: string;
  /** null closes the dialog. */
  secret: string | null;
  noun: string;
  onClose: () => void;
}) {
  return (
    <Dialog open={secret !== null} onClose={onClose} title={title}>
      <div className="space-y-4">
        <div
          role="note"
          className="flex gap-2 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-sm text-warning"
        >
          <TriangleAlert className="mt-0.5 size-4 flex-none" aria-hidden />
          <p>
            This is the only time the {noun} is shown. Copy it now and store it somewhere safe; we
            can&apos;t show it again.
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2">
          <code className="min-w-0 flex-1 font-mono text-sm break-all select-all">{secret}</code>
          <CopyButton text={secret ?? ""} label={`Copy ${noun}`} />
        </div>
        <div className="flex justify-end">
          <Button onClick={onClose}>I&apos;ve copied it</Button>
        </div>
      </div>
    </Dialog>
  );
}

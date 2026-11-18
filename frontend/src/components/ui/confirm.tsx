"use client";

import * as React from "react";

import { Dialog } from "@/components/ui/overlay";
import { Button } from "@/components/ui/primitives";

interface Ask {
  title: string;
  message: string;
  confirmLabel: string;
  danger: boolean;
  run: () => void;
}

/**
 * The app's own confirm box, in place of the browser's `window.confirm`.
 * `const { ask, dialog } = useConfirm();` render `{dialog}` once, then
 * `ask({ title, message, confirmLabel, run })` where the click handler used to call `confirm()`.
 */
export function useConfirm() {
  const [pending, setPending] = React.useState<Ask | null>(null);

  const ask = React.useCallback(
    (o: {
      title: string;
      message: string;
      confirmLabel?: string;
      danger?: boolean;
      run: () => void;
    }) => setPending({ confirmLabel: "Confirm", danger: true, ...o }),
    [],
  );
  const close = React.useCallback(() => setPending(null), []);

  const dialog = (
    <Dialog open={pending !== null} onClose={close} title={pending?.title ?? ""}>
      <p className="text-sm text-muted-foreground">{pending?.message}</p>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={close}>
          Cancel
        </Button>
        <Button
          variant={pending?.danger ? "danger" : "primary"}
          onClick={() => {
            const run = pending?.run;
            close();
            run?.();
          }}
        >
          {pending?.confirmLabel}
        </Button>
      </div>
    </Dialog>
  );

  return { ask, dialog };
}

"use client";

import { AlertTriangle, RotateCcw, Trash2, type LucideIcon } from "lucide-react";
import * as React from "react";
import { createPortal } from "react-dom";

import { Button } from "@/components/ui/primitives";
import { useFocusTrap } from "@/hooks/use-focus-trap";
import { cn } from "@/lib/utils";

/** What the box is for: picks the icon and colour. `danger` is the default. */
export type ConfirmTone = "danger" | "warning" | "neutral";

export interface ConfirmOptions {
  title: string;
  message: string;
  /** The thing being acted on (a name, an address). Shown as a highlighted chip. */
  detail?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Kept for older callers: `false` is the same as `tone: "neutral"`. */
  danger?: boolean;
  tone?: ConfirmTone;
  /** May return a promise; the box stays open with a spinner until it settles. */
  run: () => void | Promise<unknown>;
}

const TONES: Record<ConfirmTone, { icon: LucideIcon; badge: string; ring: string }> = {
  danger: { icon: Trash2, badge: "bg-danger-soft text-danger", ring: "ring-danger/15" },
  warning: { icon: AlertTriangle, badge: "bg-warning-soft text-warning", ring: "ring-warning/20" },
  neutral: { icon: RotateCcw, badge: "bg-primary/10 text-primary", ring: "ring-primary/15" },
};

/** The one confirm box for the whole app. Prefer `useConfirm`; use this directly only for controlled cases. */
export function ConfirmDialog({
  open,
  onClose,
  title,
  message,
  detail,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "danger",
  busy = false,
  onConfirm,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  message: string;
  detail?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: ConfirmTone;
  busy?: boolean;
  onConfirm: () => void;
}) {
  const ref = React.useRef<HTMLDivElement>(null);
  const cancelRef = React.useRef<HTMLButtonElement>(null);
  const titleId = React.useId();
  const messageId = React.useId();
  const close = React.useCallback(() => {
    if (!busy) onClose();
  }, [busy, onClose]);

  useFocusTrap(ref, open);

  React.useEffect(() => {
    if (!open) return;
    // Land on Cancel, so a stray Enter never destroys anything.
    cancelRef.current?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const key = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("keydown", key);
    return () => {
      document.body.style.overflow = prev;
      document.removeEventListener("keydown", key);
    };
  }, [open, close]);

  if (!open || typeof document === "undefined") return null;
  const { icon: Icon, badge, ring } = TONES[tone];

  return createPortal(
    <div
      role="presentation"
      className="fixed inset-0 z-[70] grid place-items-end bg-[#0b1226]/55 backdrop-blur-sm sm:place-items-center sm:p-4"
      onMouseDown={(e) => e.target === e.currentTarget && close()}
    >
      <div
        ref={ref}
        tabIndex={-1}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={messageId}
        className="w-full animate-rise overflow-hidden rounded-t-3xl border border-border bg-surface p-6 text-center shadow-hero outline-none sm:max-w-[26rem] sm:rounded-3xl sm:p-8"
      >
        <div
          className={cn(
            "mx-auto grid size-14 place-items-center rounded-full ring-8 ring-offset-0",
            badge,
            ring,
          )}
        >
          <Icon className="size-6" aria-hidden />
        </div>

        <h2 id={titleId} className="mt-5 text-lg font-semibold tracking-tight text-foreground">
          {title}
        </h2>
        <p id={messageId} className="mt-2 text-sm leading-relaxed text-muted-foreground">
          {message}
        </p>

        {detail && (
          <p
            className="mx-auto mt-4 max-w-full truncate rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm font-medium text-foreground-2"
            title={detail}
          >
            {detail}
          </p>
        )}

        <div className="mt-7 flex flex-col-reverse gap-2.5 sm:flex-row sm:justify-center">
          <Button
            ref={cancelRef}
            variant="outline"
            className="sm:min-w-28"
            disabled={busy}
            onClick={close}
          >
            {cancelLabel}
          </Button>
          <Button
            variant={tone === "neutral" ? "primary" : "danger"}
            className="sm:min-w-28"
            loading={busy}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/**
 * The app's own confirm box, in place of the browser's `window.confirm`.
 * `const { ask, dialog } = useConfirm();` render `{dialog}` once, then
 * `ask({ title, message, detail, confirmLabel, run })` where the click handler used to call `confirm()`.
 */
export function useConfirm() {
  const [pending, setPending] = React.useState<ConfirmOptions | null>(null);
  const [busy, setBusy] = React.useState(false);

  const ask = React.useCallback((o: ConfirmOptions) => setPending(o), []);
  const close = React.useCallback(() => {
    setPending(null);
    setBusy(false);
  }, []);

  const confirm = React.useCallback(() => {
    const run = pending?.run;
    if (!run) return;
    let result: void | Promise<unknown>;
    try {
      result = run();
    } catch (e) {
      close();
      throw e;
    }
    if (result && typeof (result as Promise<unknown>).then === "function") {
      setBusy(true);
      (result as Promise<unknown>).then(close, close);
    } else {
      close();
    }
  }, [pending, close]);

  const tone: ConfirmTone = pending?.tone ?? (pending?.danger === false ? "neutral" : "danger");

  const dialog = (
    <ConfirmDialog
      open={pending !== null}
      onClose={close}
      title={pending?.title ?? ""}
      message={pending?.message ?? ""}
      detail={pending?.detail}
      confirmLabel={pending?.confirmLabel}
      cancelLabel={pending?.cancelLabel}
      tone={tone}
      busy={busy}
      onConfirm={confirm}
    />
  );

  return { ask, dialog };
}

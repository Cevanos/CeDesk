"use client";

import { X } from "lucide-react";
import * as React from "react";
import { createPortal } from "react-dom";

import { useFocusTrap } from "@/hooks/use-focus-trap";
import { cn } from "@/lib/utils";

/** Close on outside click and Escape. */
function useDismiss(open: boolean, onClose: () => void, ref: React.RefObject<HTMLElement | null>) {
  React.useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [open, onClose, ref]);
}

/** Anchored dropdown. `trigger` receives open state and toggle props. */
export function Popover({
  trigger,
  children,
  align = "right",
  className,
}: {
  trigger: (p: {
    open: boolean;
    props: React.ButtonHTMLAttributes<HTMLButtonElement>;
  }) => React.ReactNode;
  children: (close: () => void) => React.ReactNode;
  align?: "left" | "right";
  className?: string;
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);
  const close = React.useCallback(() => setOpen(false), []);
  useDismiss(open, close, ref);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) return;
    if (e.key === "Escape") {
      setOpen(false);
      ref.current?.querySelector<HTMLElement>("[aria-haspopup]")?.focus();
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const items = Array.from(
      ref.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? [],
    );
    if (items.length === 0) return;
    e.preventDefault();
    const i = items.indexOf(document.activeElement as HTMLElement);
    const next = e.key === "ArrowDown" ? (i + 1) % items.length : (i <= 0 ? items.length : i) - 1;
    items[next]?.focus();
  };

  return (
    <div ref={ref} role="presentation" className="relative" onKeyDown={onKeyDown}>
      {trigger({
        open,
        props: {
          onClick: () => setOpen((o) => !o),
          "aria-expanded": open,
          "aria-haspopup": "menu",
        },
      })}
      {open && (
        <div
          role="menu"
          className={cn(
            "absolute top-[calc(100%+8px)] z-50 min-w-60 animate-rise rounded-xl border border-border bg-surface p-1.5 shadow-pop",
            align === "right" ? "right-0" : "left-0",
            className,
          )}
        >
          {children(close)}
        </div>
      )}
    </div>
  );
}

export function MenuItem({ className, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      role="menuitem"
      className={cn(
        "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-foreground-2 transition-colors hover:bg-surface-2 disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

/** Modal dialog: focus trapped and restored, Escape to close, scroll lock. */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  className?: string;
}) {
  const ref = React.useRef<HTMLDivElement>(null);
  const titleId = React.useId();

  useFocusTrap(ref, open);

  React.useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", key);
    return () => {
      document.body.style.overflow = prev;
      document.removeEventListener("keydown", key);
    };
  }, [open, onClose]);

  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div
      role="presentation"
      className="fixed inset-0 z-[60] grid place-items-end bg-[#0b1226]/55 p-0 backdrop-blur-sm sm:place-items-center sm:p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={cn(
          "max-h-[92svh] w-full animate-rise overflow-y-auto rounded-t-2xl border border-border bg-surface p-5 shadow-hero outline-none sm:max-w-md sm:rounded-2xl sm:p-6",
          className,
        )}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 id={titleId} className="text-lg font-semibold tracking-tight">
              {title}
            </h2>
            {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="grid size-8 flex-none place-items-center rounded-lg text-muted-foreground hover:bg-surface-2"
          >
            <X className="size-4" />
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}

"use client";

import { Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Button, Card, Skeleton } from "@/components/ui/primitives";
import { quietReason, type TicketRef } from "@/hooks/use-ticket-extras";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

/** What every extra card needs to know about the ticket. */
export interface ExtraProps {
  ticketId: string;
  /** Editing allowed (role and the ticket's `can_edit`). Disables controls when false. */
  canEdit: boolean;
}

export function ExtraCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="rounded-2xl p-4 sm:p-5">
      <h3 className="mb-3 flex items-center gap-2 text-[11px] font-bold tracking-[0.08em] text-muted-foreground uppercase after:h-px after:flex-1 after:bg-border">
        {title}
      </h3>
      {children}
    </Card>
  );
}

export function Muted({ children, className }: { children: React.ReactNode; className?: string }) {
  return <p className={cn("text-sm text-muted-foreground", className)}>{children}</p>;
}

/** Loading, quiet (403/404/402/503) and retryable error states for an optional card. */
export function QueryState({
  q,
}: {
  q: { isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };
}) {
  if (q.isPending) return <Skeleton className="h-8" />;
  if (!q.isError) return null;
  const quiet = quietReason(q.error);
  if (quiet) return <Muted>{quiet}</Muted>;
  return (
    <div className="text-sm text-muted-foreground">
      Couldn&apos;t load this.{" "}
      <button className="font-medium text-primary-ink hover:underline" onClick={() => q.refetch()}>
        Retry
      </button>
    </div>
  );
}

export function errText(e: unknown) {
  if (e instanceof ApiError) {
    return e.details.length ? e.details.map((d) => d.message).join(" ") : e.userMessage;
  }
  return "Something went wrong.";
}
export const toastError = (e: unknown) => {
  toast.error(errText(e));
};

/** A small "+ Add" button that reveals a form. */
export function AddToggle({
  label,
  disabled,
  children,
}: {
  label: string;
  disabled?: boolean;
  children: (close: () => void) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  if (open) return <div className="mt-3 w-full">{children(() => setOpen(false))}</div>;
  return (
    <Button variant="outline" size="sm" disabled={disabled} onClick={() => setOpen(true)}>
      <Plus className="size-3.5" /> {label}
    </Button>
  );
}

export function TicketLink({ t }: { t: TicketRef }) {
  return (
    <Link
      href={`/tickets/${encodeURIComponent(t.id)}`}
      className="block min-w-0 rounded-lg px-2 py-1.5 text-sm hover:bg-surface-2"
    >
      <span className="font-mono text-xs font-semibold text-muted-foreground">
        {t.label ?? t.id.slice(0, 8)}
      </span>{" "}
      <span className="wrap-break-word">{t.subject ?? "Ticket"}</span>
      {t.status && <span className="ml-1 text-xs text-subtle-foreground">· {t.status}</span>}
    </Link>
  );
}

export function fmtMinutes(m: number) {
  const h = Math.floor(m / 60);
  const r = m % 60;
  return h ? `${h}h${r ? ` ${r}m` : ""}` : `${r}m`;
}

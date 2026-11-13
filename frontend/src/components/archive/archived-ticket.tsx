"use client";

import { ArrowLeft, Lock, Paperclip } from "lucide-react";
import Link from "next/link";

import { ticketName } from "@/components/archive/archive-search";
import { DetailSkeleton } from "@/components/feedback/skeletons";
import { CsatCard } from "@/components/tickets/csat";
import { formatBytes } from "@/components/tickets/message-files";
import { Badge, Card, EmptyState } from "@/components/ui/primitives";
import { useArchivedTicket } from "@/hooks/use-archive";
import { ApiError } from "@/lib/api/client";
import type { Message } from "@/lib/api/types";
import { cn, formatDateTime } from "@/lib/utils";

/** A message in the read-only thread: no delivery state, no actions, file names only. */
function ReadOnlyMessage({ m }: { m: Message }) {
  if (m.kind === "system") {
    return <p className="py-1 text-center text-xs text-subtle-foreground">{m.body}</p>;
  }
  const customer = m.kind === "customer" || (m.kind !== "ai" && m.author_member_id === null);
  const note = m.kind === "note";
  const files = m.attachments ?? [];
  return (
    <div className={cn("flex", customer || m.kind === "ai" ? "justify-start" : "justify-end")}>
      <div
        className={cn(
          "max-w-[88%] rounded-2xl border px-4 py-3 shadow-card sm:max-w-[75%]",
          note
            ? "border-note-border bg-note"
            : customer
              ? "rounded-tl-sm border-border bg-surface"
              : "rounded-tr-sm border-primary/20 bg-primary-soft",
        )}
      >
        <div className="mb-1 flex flex-wrap items-center gap-x-2 text-[11px] text-muted-foreground">
          <span className="font-semibold text-foreground-2">
            {m.author_name ?? (m.kind === "ai" ? "AI assistant" : customer ? "Customer" : "Agent")}
          </span>
          {note && (
            <span className="inline-flex items-center gap-1 font-medium text-warning">
              <Lock className="size-3" /> Private note
            </span>
          )}
          <time dateTime={m.created_at}>{formatDateTime(m.created_at)}</time>
        </div>
        {m.body && (
          <p className="text-sm leading-relaxed wrap-break-word whitespace-pre-wrap">{m.body}</p>
        )}
        {files.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Files">
            {files.map((f, i) => (
              <li
                key={f.id ?? i}
                className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-border bg-surface-2 px-2.5 py-1 text-xs"
              >
                <Paperclip className="size-3 flex-none" aria-hidden />
                <span className="min-w-0 truncate">{f.name}</span>
                {f.size != null && (
                  <span className="text-muted-foreground">{formatBytes(f.size)}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export function ArchivedTicketView({ id }: { id: string }) {
  const q = useArchivedTicket(id);

  if (q.isLoading) return <DetailSkeleton />;
  if (q.isError || !q.data) {
    const notFound = q.error instanceof ApiError && q.error.status === 404;
    return (
      <div className="mx-auto max-w-3xl px-3 py-8 sm:px-6">
        <BackLink />
        <Card className="mt-4" role={notFound ? undefined : "alert"}>
          <EmptyState title={notFound ? "Not found" : "Couldn't open this ticket"}>
            {q.error instanceof ApiError ? q.error.userMessage : "Please try again."}
          </EmptyState>
        </Card>
      </div>
    );
  }

  const t = q.data;
  const messages = [...(t.messages ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const csat =
    t.csat ??
    (t.rating && typeof t.rating === "object"
      ? { score: t.rating.score, comment: t.rating.comment }
      : typeof t.rating === "number"
        ? { score: t.rating }
        : null);
  // Files that are not tied to a message (older data) are still listed by name.
  const looseFiles = t.attachments ?? [];

  return (
    <div className="mx-auto w-full max-w-3xl px-3 py-5 sm:px-6 sm:py-8">
      <BackLink />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm text-muted-foreground">{ticketName(t)}</span>
        <Badge>Archived · read only</Badge>
      </div>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight wrap-break-word">
        {t.subject || "(no subject)"}
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        {t.customer_name ?? t.contact_name ?? ""}
        {(t.customer_email ?? t.contact_email) ? ` · ${t.customer_email ?? t.contact_email}` : ""}
        {t.resolved_at ? ` · Resolved ${formatDateTime(t.resolved_at)}` : ""}
      </p>

      <section aria-label="Conversation" className="mt-6 space-y-3">
        {messages.length === 0 ? (
          <Card>
            <EmptyState title="No messages">This archived ticket has no messages.</EmptyState>
          </Card>
        ) : (
          messages.map((m) => <ReadOnlyMessage key={m.id} m={m} />)
        )}
      </section>

      {looseFiles.length > 0 && (
        <Card className="mt-6 p-4">
          <h2 className="text-sm font-semibold">Files</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {looseFiles.map((f, i) => (
              <li key={f.id ?? i} className="flex items-center gap-2">
                <Paperclip className="size-3.5 text-muted-foreground" aria-hidden />
                <span className="min-w-0 truncate">{f.name}</span>
                {f.size != null && (
                  <span className="text-xs text-muted-foreground">{formatBytes(f.size)}</span>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {csat && (
        <Card className="mt-6 p-4">
          <h2 className="mb-2 text-sm font-semibold">Customer rating</h2>
          <CsatCard csat={csat} />
        </Card>
      )}
    </div>
  );
}

function BackLink() {
  return (
    <Link
      href="/archive"
      className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="size-4" /> Archive
    </Link>
  );
}

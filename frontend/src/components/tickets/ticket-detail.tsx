"use client";

import { ArrowLeft, CheckCircle2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { AiSummaryCard } from "@/components/tickets/ai-assist";
import { PriorityBadge, StatusBadge, VipBadge, languageName } from "@/components/tickets/badges";
import { Composer } from "@/components/tickets/composer";
import { FollowButton } from "@/components/tickets/inbox/followers";
import { SnoozeBanner, SnoozeButton } from "@/components/tickets/inbox/snooze";
import { TicketMoreMenu } from "@/components/tickets/inbox/transcript";
import { JiraHeaderChip } from "@/components/tickets/jira-card";
import { MacrosMenu } from "@/components/tickets/macros-menu";
import { ReceiptPanel, ResolveDialog, useReceiptPolicy } from "@/components/tickets/resolution";
import { TicketSidebar } from "@/components/tickets/sidebar";
import { SlaPill } from "@/components/tickets/sla";
import { AcceptCard, HandoverCard, Thread } from "@/components/tickets/thread";
import { Badge, Button, EmptyState, Skeleton } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { useTicket, useResolveTicket } from "@/hooks/use-tickets";
import { useAutomationAccess } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import { inboxActions } from "@/lib/permissions";
import { ticketListHref } from "@/lib/ticket-list-memory";

export function TicketDetail({ id }: { id: string }) {
  const { user, perms } = useCurrentUser();
  const actions = inboxActions(user);
  const automation = useAutomationAccess();
  const q = useTicket(id);
  const resolve = useResolveTicket(id);
  const policy = useReceiptPolicy(q.data?.ticket.team_id);
  const [receiptOpen, setReceiptOpen] = useState(false);

  if (q.isPending) {
    return (
      <div className="mx-auto max-w-6xl space-y-4 p-4 sm:p-6">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (q.isError) {
    const notFound = q.error instanceof ApiError && q.error.status === 404;
    return (
      <EmptyState title={notFound ? "Ticket not found" : "We couldn't load this ticket"}>
        {notFound
          ? "It may have been removed, or you may not have access."
          : q.error instanceof ApiError
            ? q.error.userMessage
            : "Please try again."}
        <div className="mt-4 flex justify-center gap-2">
          <Link href="/tickets">
            <Button variant="outline" size="sm">
              Back to tickets
            </Button>
          </Link>
          {notFound && (
            <Link href="/archive">
              <Button variant="outline" size="sm">
                Search the archive
              </Button>
            </Link>
          )}
          {!notFound && (
            <Button size="sm" onClick={() => q.refetch()}>
              Retry
            </Button>
          )}
        </div>
      </EmptyState>
    );
  }

  const { ticket, etag } = q.data;
  const viewOnlyReason =
    (ticket as { can_edit?: boolean; view_only_reason?: string | null }).can_edit === false
      ? ((ticket as { view_only_reason?: string | null }).view_only_reason ??
        "This ticket is view-only for you.")
      : null;

  // Same rule as the status dropdown in the side panel.
  const canChange = perms.editTicket && (ticket as { can_edit?: boolean }).can_edit !== false;

  return (
    <div className="mx-auto flex min-h-[calc(100svh-3.5rem)] w-full max-w-6xl flex-col px-3 pt-4 sm:px-6 sm:pt-6">
      <nav
        aria-label="Breadcrumb"
        className="mb-4 flex items-center gap-1.5 text-[13px] text-muted-foreground"
      >
        <Link
          href={ticketListHref()}
          className="inline-flex items-center gap-1 rounded-md py-0.5 pr-1.5 transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" /> Tickets
        </Link>
        <span aria-hidden className="text-subtle-foreground">
          /
        </span>
        <span className="font-mono font-medium text-foreground-2">{ticket.label}</span>
      </nav>

      <header className="relative rounded-2xl border border-border bg-surface shadow-card">
        <div
          aria-hidden
          className="absolute inset-x-0 top-0 h-1 rounded-t-2xl bg-linear-to-r from-primary via-primary/60 to-transparent"
        />
        <div className="flex flex-wrap items-start gap-4 p-4 sm:p-6">
          <div className="min-w-0 flex-1 basis-72">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-md bg-surface-2 px-2 py-0.5 font-mono text-xs font-semibold text-muted-foreground">
                {ticket.label}
              </span>
              <StatusBadge status={ticket.status} />
              {ticket.source === "demo" && <Badge tone="info">Demo</Badge>}
              <PriorityBadge priority={ticket.priority} />
              {ticket.contact.vip && <VipBadge />}
              {ticket.accepted === false && <Badge tone="warning">Waiting to accept</Badge>}
              {ticket.topic && <Badge tone="primary">{ticket.topic}</Badge>}
              {ticket.language && (
                <Badge title={ticket.language}>{languageName(ticket.language)}</Badge>
              )}
              <JiraHeaderChip ticketId={ticket.id} />
              <SlaPill sla={ticket.sla} />
            </div>
            <h1 className="mt-3 text-xl font-semibold tracking-tight text-balance wrap-break-word sm:text-[26px] sm:leading-8">
              {ticket.subject}
            </h1>
            {ticket.contact?.name && (
              <p className="mt-2 text-sm text-muted-foreground">
                from <span className="font-medium text-foreground-2">{ticket.contact.name}</span>
                {ticket.contact.email && (
                  <span className="text-subtle-foreground"> · {ticket.contact.email}</span>
                )}
              </p>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="icon"
              aria-label="Reload ticket"
              onClick={() => q.refetch()}
              disabled={q.isFetching}
            >
              <RefreshCw className={q.isFetching ? "size-4 animate-spin" : "size-4"} />
            </Button>
            <FollowButton ticketId={ticket.id} />
            {perms.reply && ticket.accepted !== false && (
              <MacrosMenu ticketId={ticket.id} etag={etag} />
            )}
            {canChange && ticket.status !== "resolved" && (
              <SnoozeButton ticketId={ticket.id} etag={etag} />
            )}
            {((actions.sendTranscript && canChange) || automation.settings) && (
              <TicketMoreMenu ticket={ticket} canTranscript={actions.sendTranscript && canChange} />
            )}
            {perms.resolve && ticket.status !== "resolved" && (
              <Button
                loading={resolve.isPending}
                onClick={() =>
                  policy.form
                    ? setReceiptOpen(true)
                    : resolve.mutate(
                        { etag },
                        { onSuccess: () => toast.success(`${ticket.label} resolved`) },
                      )
                }
              >
                <CheckCircle2 className="size-4" /> Resolve
              </Button>
            )}
          </div>
        </div>
      </header>

      {receiptOpen && (
        <ResolveDialog
          ticketId={ticket.id}
          label={ticket.label}
          etag={etag}
          required={policy.required}
          onClose={() => setReceiptOpen(false)}
        />
      )}

      {ticket.snoozed_until && (
        <SnoozeBanner ticketId={ticket.id} until={ticket.snoozed_until} canEdit={canChange} />
      )}

      {viewOnlyReason && (
        <div
          role="status"
          className="mt-4 rounded-xl border border-warning/30 bg-warning-soft px-4 py-2.5 text-sm text-warning"
        >
          {viewOnlyReason}
        </div>
      )}

      <div className="mt-5 grid flex-1 gap-5 lg:grid-cols-[minmax(0,1fr)_21rem]">
        <section className="flex min-w-0 flex-col gap-4" aria-label="Conversation">
          {ticket.status === "resolved" && (
            <ReceiptPanel
              ticketId={ticket.id}
              resolution={ticket.resolution}
              canChange={canChange}
            />
          )}
          <HandoverCard ticket={ticket} />
          <AcceptCard ticket={ticket} />
          <AiSummaryCard ticketId={ticket.id} />
          <div className="flex-1">
            <Thread ticket={ticket} myId={user?.id} />
          </div>
          <div className="sticky bottom-0 z-10 -mx-1 mt-auto bg-linear-to-t from-background from-70% to-transparent px-1 pt-6 pb-3">
            <Composer
              ticketId={ticket.id}
              canReply={perms.reply}
              accepted={ticket.accepted !== false}
              channel={ticket.channel}
              replyCc={ticket.reply_cc}
            />
          </div>
        </section>
        <aside aria-label="Ticket details" className="min-w-0 pb-6">
          <TicketSidebar ticket={ticket} etag={etag} />
        </aside>
      </div>
    </div>
  );
}

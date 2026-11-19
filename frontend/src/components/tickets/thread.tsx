"use client";

import { AlertCircle, Check, CheckCircle2, Loader2, Lock, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { TranslateMessage } from "@/components/tickets/ai-assist";
import { FileChip } from "@/components/tickets/message-files";
import { Badge, Button, Card } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { useAcceptTicket, useOlderMessages } from "@/hooks/use-tickets";
import type { Delivery, Message, Ticket } from "@/lib/api/types";
import { cn, formatDateTime, initials } from "@/lib/utils";

/** Handover box: shown only when the ticket came from the AI. */
export function HandoverCard({ ticket }: { ticket: Ticket }) {
  const h = ticket.handover;
  if (!h) return null;
  return (
    <Card className="overflow-hidden rounded-2xl border-primary/25 shadow-pop">
      <div className="flex flex-wrap items-center gap-2 border-b border-primary/15 bg-linear-to-r from-primary-soft to-surface px-4 py-3">
        <span className="grid size-6 place-items-center rounded-md bg-primary text-primary-foreground">
          <Sparkles className="size-3.5" />
        </span>
        <span className="text-xs font-bold tracking-wider text-primary-ink uppercase">
          AI handover
        </span>
        <div className="ml-auto flex flex-wrap gap-1.5">
          {ticket.topic && <Badge tone="primary">{ticket.topic}</Badge>}
          {ticket.sentiment && <Badge tone="warning">{ticket.sentiment}</Badge>}
          {ticket.language && <Badge>{ticket.language}</Badge>}
        </div>
      </div>
      <dl className="grid gap-x-4 gap-y-2 p-4 text-sm sm:grid-cols-[8rem_1fr]">
        {Object.entries(h).map(([k, v]) =>
          v == null || v === "" ? null : (
            <div key={k} className="contents">
              <dt className="text-muted-foreground capitalize">{k.replace(/_/g, " ")}</dt>
              <dd className="break-words">
                {typeof v === "object" ? JSON.stringify(v) : String(v)}
              </dd>
            </div>
          ),
        )}
      </dl>
      <AcceptBar ticket={ticket} />
    </Card>
  );
}

/** Accept control for an unaccepted ticket that has no handover box to hold it. */
export function AcceptCard({ ticket }: { ticket: Ticket }) {
  if (ticket.handover || ticket.accepted !== false) return null;
  return (
    <Card className="overflow-hidden rounded-2xl border-primary/25">
      <AcceptBar ticket={ticket} />
    </Card>
  );
}

/** Replies stay locked (409) until an agent accepts the handover; notes still work. */
function AcceptBar({ ticket }: { ticket: Ticket }) {
  const { perms } = useCurrentUser();
  const accept = useAcceptTicket(ticket.id);
  if (ticket.accepted === undefined) return null;
  if (ticket.accepted) {
    return (
      <div className="flex items-center gap-2 border-t border-border px-4 py-3 text-sm text-success">
        <CheckCircle2 className="size-4" aria-hidden /> Accepted
        {ticket.assignee_name && (
          <span className="text-muted-foreground">by {ticket.assignee_name}</span>
        )}
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-3 border-t border-border px-4 py-3">
      <Button
        size="sm"
        loading={accept.isPending}
        disabled={!perms.reply}
        title={perms.reply ? undefined : "Light agents can't accept chats"}
        onClick={() =>
          accept.mutate(undefined, { onSuccess: () => toast.success("Chat accepted") })
        }
      >
        Accept
      </Button>
      <span className="text-xs text-muted-foreground">
        Accept to reply to the customer. You can still leave private notes.
      </span>
    </div>
  );
}

function DeliveryStatus({ delivery }: { delivery: Delivery }) {
  if (delivery === "failed") {
    return (
      <span className="inline-flex items-center gap-1 font-medium text-danger">
        <AlertCircle className="size-3" aria-hidden /> Not delivered
      </span>
    );
  }
  if (delivery === "sent") {
    return (
      <span className="inline-flex items-center gap-1 text-success">
        <Check className="size-3" aria-hidden /> Emailed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1">
      <Loader2 className="size-3 animate-spin" aria-hidden /> Sending…
    </span>
  );
}

function Bubble({ m, mine }: { m: Message; mine: boolean }) {
  if (m.kind === "system") {
    return (
      <p className="mx-auto max-w-[92%] py-1 text-center text-xs text-subtle-foreground">
        {m.body}
      </p>
    );
  }
  const ai = m.kind === "ai";
  const note = m.kind === "note";
  // Customers: kind "customer" (or, for older data, a message with no author).
  const customer = m.kind === "customer" || (!ai && !note && m.author_member_id === null);
  const left = customer || ai;
  const avatar = (
    <span
      className={cn(
        "mt-0.5 grid size-8 flex-none place-items-center rounded-full text-[11px] font-bold ring-2 ring-surface",
        ai
          ? "bg-primary-soft text-primary-ink"
          : customer
            ? "bg-surface-3 text-muted-foreground"
            : "bg-primary text-primary-foreground",
      )}
    >
      {ai ? (
        <Sparkles className="size-4" aria-hidden />
      ) : (
        initials(m.author_name ?? (customer ? "C" : "A"))
      )}
    </span>
  );
  const files = m.attachments ?? [];
  // Imported messages only. Information only: replies never use it.
  const recipientLine = m.recipients
    ? (
        [
          ["To", m.recipients.to],
          ["Cc", m.recipients.cc],
          ["Bcc", m.recipients.bcc],
        ] as const
      )
        .filter(([, v]) => v && v.length > 0)
        .map(([k, v]) => `${k}: ${v!.join(", ")}`)
        .join(" · ")
    : "";
  return (
    <div className={cn("flex gap-2.5", left ? "justify-start" : "justify-end")}>
      {left && avatar}
      <div
        className={cn(
          "max-w-[85%] rounded-2xl border px-4 py-3 shadow-card sm:max-w-[75%]",
          note
            ? "border-note-border bg-note"
            : ai
              ? "rounded-tl-sm border-primary/20 bg-primary-soft/50"
              : customer
                ? "rounded-tl-sm border-border bg-surface"
                : mine
                  ? "rounded-tr-sm border-primary/20 bg-primary-soft"
                  : "rounded-tr-sm border-border bg-surface-2",
        )}
      >
        <div className="mb-1 flex flex-wrap items-center gap-x-2 text-[11px] text-muted-foreground">
          <span className="font-semibold text-foreground-2">
            {m.author_name ?? (ai ? "AI assistant" : customer ? "Customer" : "Agent")}
          </span>
          {ai && <Badge tone="primary">AI</Badge>}
          {note && (
            <span className="inline-flex items-center gap-1 font-medium text-warning">
              <Lock className="size-3" /> Private note
            </span>
          )}
          <time dateTime={m.created_at}>{formatDateTime(m.created_at)}</time>
          {m.delivery && !left && <DeliveryStatus delivery={m.delivery} />}
        </div>
        {recipientLine && <p className="mb-1 text-[11px] text-muted-foreground">{recipientLine}</p>}
        {m.body && (
          <p className="text-sm leading-relaxed wrap-break-word whitespace-pre-wrap">{m.body}</p>
        )}
        {m.body && <TranslateMessage text={m.body} />}
        {files.length > 0 && (
          <ul className={cn("flex flex-wrap gap-1.5", m.body && "mt-2.5")} aria-label="Attachments">
            {files.map((f) => (
              <li key={f.id} className="max-w-full">
                <FileChip file={f} />
              </li>
            ))}
          </ul>
        )}
      </div>
      {!left && avatar}
    </div>
  );
}

export function Thread({ ticket, myId }: { ticket: Ticket; myId?: string }) {
  const older = useOlderMessages(ticket.id);
  const [prepended, setPrepended] = useState<Message[]>([]);
  const [cursor, setCursor] = useState<string | null | undefined>(undefined);

  const nextCursor = cursor === undefined ? ticket.older_messages_cursor : cursor;

  const messages = useMemo(() => {
    const byId = new Map<string, Message>();
    for (const m of [...prepended, ...(ticket.messages ?? [])]) byId.set(m.id, m);
    return [...byId.values()].sort((a, b) => a.created_at.localeCompare(b.created_at));
  }, [prepended, ticket.messages]);

  const loadOlder = () => {
    if (!nextCursor) return;
    older.mutate(nextCursor, {
      onSuccess: (page) => {
        setPrepended((p) => [...page.messages, ...p]);
        setCursor(page.next_cursor ?? null);
      },
    });
  };

  return (
    <div className="space-y-3">
      {nextCursor && (
        <div className="text-center">
          <Button variant="outline" size="sm" onClick={loadOlder} disabled={older.isPending}>
            {older.isPending && <Loader2 className="size-4 animate-spin" />} Load older messages
          </Button>
        </div>
      )}
      {messages.length === 0 && (
        <p className="py-8 text-center text-sm text-muted-foreground">No messages yet.</p>
      )}
      {messages.map((m) => (
        <Bubble key={m.id} m={m} mine={m.author_member_id === myId} />
      ))}
    </div>
  );
}

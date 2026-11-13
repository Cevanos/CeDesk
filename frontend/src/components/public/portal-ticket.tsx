"use client";

import { ArrowLeft, CheckCircle2, LifeBuoy, Send, UserRound } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { publicErrorMessage } from "@/components/public/errors";
import { PortalSignIn } from "@/components/public/portal-signin";
import { RatingPicker, scoreLabel } from "@/components/public/rating";
import { StatusBadge } from "@/components/tickets/badges";
import { FileChip } from "@/components/tickets/message-files";
import { Button, Card, EmptyState, Field, Skeleton, Textarea } from "@/components/ui/primitives";
import {
  isUnauthorized,
  usePortalTicket,
  useRatePortalTicket,
  useSendPortalMessage,
  useWorkspaceSlug,
} from "@/hooks/use-portal";
import type { PortalMessage } from "@/lib/api/types";
import { cn, formatDateTime } from "@/lib/utils";

/**
 * `/<workspace>/tickets/<id>`: where the reply emails send customers. Signed out, they get
 * the code sign-in first; once signed in the same page shows the ticket.
 */
export function PortalTicketPage() {
  const ws = useWorkspaceSlug();
  const { id } = useParams<{ id: string }>();
  const ticket = usePortalTicket(id);

  if (ticket.isPending) {
    return (
      <div className="mx-auto max-w-3xl space-y-3">
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-28 rounded-2xl" />
        <Skeleton className="h-28 rounded-2xl" />
      </div>
    );
  }
  if (isUnauthorized(ticket.error)) {
    return (
      <PortalSignIn
        title="Sign in to see your ticket"
        intro="Enter the email address you wrote to us with. We'll send a 6-digit code to it."
      />
    );
  }
  if (ticket.isError) {
    return (
      <Card className="mx-auto max-w-xl rounded-2xl">
        <EmptyState icon={<LifeBuoy className="size-6" />} title="We can't show this ticket">
          {publicErrorMessage(ticket.error)}
        </EmptyState>
      </Card>
    );
  }

  const t = ticket.data;
  const resolved = t.status === "resolved";
  return (
    <div className="mx-auto max-w-3xl animate-rise">
      <Link
        href={`/${encodeURIComponent(ws)}/portal`}
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> All tickets
      </Link>
      <div className="mt-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wider text-subtle-foreground uppercase">
            {t.label}
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-balance sm:text-3xl">
            {t.subject}
          </h1>
        </div>
        <StatusBadge status={t.status} label={t.status_label} />
      </div>

      <ol aria-label="Conversation" className="mt-8 space-y-4">
        {t.messages.map((m) => (
          <Bubble key={m.id} m={m} />
        ))}
      </ol>

      {!resolved && <ReplyBox id={t.id} />}
      {resolved && <Rating id={t.id} ticket={t} />}
    </div>
  );
}

function Bubble({ m }: { m: PortalMessage }) {
  const mine = m.from_customer;
  return (
    <li className={cn("flex gap-3", mine && "flex-row-reverse")}>
      <span
        aria-hidden
        className={cn(
          "mt-1 grid size-9 flex-none place-items-center rounded-full",
          mine ? "bg-primary text-primary-foreground" : "bg-surface-3 text-muted-foreground",
        )}
      >
        {mine ? <UserRound className="size-4" /> : <LifeBuoy className="size-4" />}
      </span>
      <div className={cn("min-w-0 max-w-[85%]", mine && "text-right")}>
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground-2">
            {mine ? "You" : (m.author || "Support team")}
          </span>{" "}
          · {formatDateTime(m.created_at)}
        </p>
        <div
          className={cn(
            "mt-1 inline-block rounded-2xl px-4 py-3 text-left text-[14.5px] leading-relaxed break-words whitespace-pre-wrap shadow-card",
            mine
              ? "rounded-tr-md bg-primary-soft text-foreground"
              : "rounded-tl-md border border-border bg-surface",
          )}
        >
          {m.body}
        </div>
        {m.attachments && m.attachments.length > 0 && (
          <ul
            aria-label="Files"
            className={cn("mt-2 flex flex-wrap gap-1.5", mine && "justify-end")}
          >
            {m.attachments.map((f) => (
              <li key={f.id} className="max-w-full">
                <FileChip file={f} portal />
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

function ReplyBox({ id }: { id: string }) {
  const send = useSendPortalMessage(id);
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);

  return (
    <Card className="mt-8 rounded-2xl p-4 shadow-pop sm:p-5">
      <form
        noValidate
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (!body.trim()) return setError("Write your message first.");
          setError(null);
          send.mutate(
            { body: body.trim() },
            { onSuccess: () => setBody(""), onError: (err) => setError(publicErrorMessage(err)) },
          );
        }}
      >
        <Field label="Reply" htmlFor="pr-body">
          <Textarea
            id="pr-body"
            rows={4}
            maxLength={10000}
            placeholder="Write your reply…"
            value={body}
            aria-invalid={!!error}
            onChange={(e) => setBody(e.target.value)}
          />
        </Field>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end">
          <Button type="submit" loading={send.isPending}>
            <Send className="size-4" /> Send reply
          </Button>
        </div>
      </form>
    </Card>
  );
}

function Rating({
  id,
  ticket,
}: {
  id: string;
  ticket: { csat?: { score: number; comment?: string | null } | null; can_rate?: boolean };
}) {
  const rate = useRatePortalTicket(id);
  const [score, setScore] = useState<number | null>(null);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (ticket.csat) {
    return (
      <Card className="mt-8 rounded-2xl p-5">
        <div role="status" className="flex items-start gap-3">
          <CheckCircle2 className="mt-0.5 size-5 flex-none text-success" aria-hidden />
          <div>
            <p className="font-medium">
              Thanks for rating us: {ticket.csat.score} of 5, {scoreLabel(ticket.csat.score)}.
            </p>
            {ticket.csat.comment && (
              <p className="mt-1 text-sm text-muted-foreground">“{ticket.csat.comment}”</p>
            )}
          </div>
        </div>
      </Card>
    );
  }
  if (!ticket.can_rate) {
    return (
      <p className="mt-8 rounded-xl bg-surface-2 p-4 text-sm text-muted-foreground">
        This ticket is resolved. If you still need help, send us a new message from your tickets
        page.
      </p>
    );
  }

  return (
    <Card className="mt-8 rounded-2xl p-5 shadow-pop sm:p-6">
      <h2 className="text-lg font-semibold tracking-tight">This ticket is resolved</h2>
      <form
        noValidate
        className="mt-4 space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (score === null) return setError("Choose a rating from 1 to 5.");
          setError(null);
          rate.mutate(
            { score, comment: comment.trim() || undefined },
            { onError: (err) => setError(publicErrorMessage(err)) },
          );
        }}
      >
        <RatingPicker value={score} onChange={setScore} disabled={rate.isPending} />
        <Field label="Anything to add? (optional)" htmlFor="rt-comment">
          <Textarea
            id="rt-comment"
            rows={3}
            maxLength={2000}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
        </Field>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <Button type="submit" loading={rate.isPending}>
          Send my rating
        </Button>
      </form>
    </Card>
  );
}

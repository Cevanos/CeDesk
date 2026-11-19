"use client";

import { Mail, MessageSquare } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
  AddToggle,
  ExtraCard,
  Muted,
  QueryState,
  errText,
  toastError,
  type ExtraProps,
} from "@/components/tickets/extras-shared";
import { LinkifiedText } from "@/components/ui/linkified-text";
import { Button, Input, Select, Textarea } from "@/components/ui/primitives";
import {
  useSideConversations,
  useSideReply,
  useStartSideEmail,
  useStartSideSlack,
  type SideConversation,
} from "@/hooks/use-ticket-extras";
import { ApiError } from "@/lib/api/client";
import { formatDateTime } from "@/lib/utils";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function StartForm({ ticketId, close }: { ticketId: string; close: () => void }) {
  const email = useStartSideEmail(ticketId);
  const slack = useStartSideSlack(ticketId);
  const [via, setVia] = useState<"email" | "slack">("email");
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [channel, setChannel] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const pending = email.isPending || slack.isPending;

  const onError = (e: unknown) => {
    if (via === "slack" && e instanceof ApiError) {
      if (e.status === 409) return setError("Install Slack in Settings → Apps.");
      if (e.status === 422)
        return setError("That channel doesn't exist, or the bot isn't in it. Check the name.");
      if (e.status === 503) return setError("Slack didn't answer. Please try again.");
    }
    setError(errText(e));
  };
  const onSuccess = () => {
    toast.success("Side conversation started");
    close();
  };

  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        if (!body.trim()) return;
        if (via === "email") {
          const list = to
            .split(/[,;\s]+/)
            .map((s) => s.trim())
            .filter(Boolean);
          if (!list.length || !list.every((a) => EMAIL_RE.test(a)))
            return setError("Enter valid email addresses.");
          if (!subject.trim()) return setError("Add a subject.");
          email.mutate(
            { to: list, subject: subject.trim(), body: body.trim() },
            { onSuccess, onError },
          );
        } else {
          if (!channel.trim()) return setError("Enter a Slack channel.");
          slack.mutate({ channel: channel.trim(), body: body.trim() }, { onSuccess, onError });
        }
      }}
    >
      <Select
        value={via}
        onChange={(e) => setVia(e.target.value as "email" | "slack")}
        aria-label="Start by"
      >
        <option value="email">By email</option>
        <option value="slack">In Slack</option>
      </Select>
      {via === "email" ? (
        <>
          <Input
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="To (comma-separated emails)"
            aria-label="To"
            className="h-9"
          />
          <Input
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="Subject"
            aria-label="Subject"
            maxLength={200}
            className="h-9"
          />
        </>
      ) : (
        <Input
          value={channel}
          onChange={(e) => setChannel(e.target.value)}
          placeholder="#channel"
          aria-label="Slack channel"
          className="h-9"
        />
      )}
      <Textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="Message"
        aria-label="Message"
        className="min-h-20"
      />
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" loading={pending}>
          Send
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={close}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function Side({
  s,
  ticketId,
  canEdit,
}: {
  s: SideConversation;
  ticketId: string;
  canEdit: boolean;
}) {
  const reply = useSideReply(ticketId);
  const [body, setBody] = useState("");
  const slack = s.via === "slack";
  const Icon = slack ? MessageSquare : Mail;
  const to = Array.isArray(s.to) ? s.to.join(", ") : s.to;
  return (
    <li className="rounded-xl border border-border p-3 text-sm">
      <div className="flex items-center gap-2 font-medium">
        <Icon className="size-3.5 flex-none text-muted-foreground" aria-hidden />
        <span className="min-w-0 flex-1 truncate">
          {s.subject || (slack ? (s.channel ?? "Slack") : "Side conversation")}
        </span>
        <span className="text-xs font-normal text-muted-foreground">
          {slack ? "Slack" : "Email"}
        </span>
      </div>
      {to && (
        <div className="text-xs wrap-break-word text-muted-foreground">
          {slack ? `Channel ${to}` : `To ${to}`}
        </div>
      )}
      <ul className="mt-2 space-y-2">
        {s.messages.map((m, i) => (
          <li
            key={m.id ?? i}
            className={
              m.direction === "in"
                ? "ml-4 rounded-lg border border-border bg-surface px-2.5 py-2"
                : "mr-4 rounded-lg bg-surface-2 px-2.5 py-2"
            }
          >
            <div className="text-xs text-muted-foreground">
              {m.author_name ?? m.from ?? ""}
              {m.direction === "in" && ` (${slack ? "in Slack" : "by email"})`}
              {m.created_at && ` ${formatDateTime(m.created_at)}`}
            </div>
            <div className="wrap-break-word whitespace-pre-wrap">
              <LinkifiedText text={m.body ?? ""} />
            </div>
          </li>
        ))}
      </ul>
      {canEdit && (
        <form
          className="mt-2 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!body.trim()) return;
            reply.mutate(
              { side_id: s.id, body: body.trim() },
              { onSuccess: () => setBody(""), onError: toastError },
            );
          }}
        >
          <Input
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Write back"
            aria-label="Write back"
            className="h-9"
          />
          <Button
            type="submit"
            size="sm"
            className="h-9"
            loading={reply.isPending}
            disabled={!body.trim()}
          >
            Send
          </Button>
        </form>
      )}
    </li>
  );
}

/** Agents only: never shown to the customer. */
export function SideConversations({ ticketId, canEdit }: ExtraProps) {
  const q = useSideConversations(ticketId);
  return (
    <ExtraCard title="Side conversations">
      <QueryState q={q} />
      {q.data && (
        <>
          {q.data.length ? (
            <ul className="mb-3 space-y-3">
              {q.data.map((s) => (
                <Side key={s.id} s={s} ticketId={ticketId} canEdit={canEdit} />
              ))}
            </ul>
          ) : (
            <Muted className="mb-3">None yet. The customer never sees these.</Muted>
          )}
          <div className="flex">
            <AddToggle label="New side conversation" disabled={!canEdit}>
              {(close) => <StartForm ticketId={ticketId} close={close} />}
            </AddToggle>
          </div>
        </>
      )}
    </ExtraCard>
  );
}

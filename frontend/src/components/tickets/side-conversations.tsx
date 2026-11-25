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
import { bind, bindSelect, fieldError, useFormik, yup } from "@/lib/forms";
import { formatDateTime } from "@/lib/utils";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const splitEmails = (v: string) =>
  v
    .split(/[,;\s]+/)
    .map((x) => x.trim())
    .filter(Boolean);

const startSchema = yup.object({
  via: yup.string().oneOf(["email", "slack"]).required(),
  to: yup.string().when("via", {
    is: "email",
    then: (s) =>
      s.test("emails", "Enter valid email addresses.", (v) => {
        const list = splitEmails(v ?? "");
        return list.length > 0 && list.every((a) => EMAIL_RE.test(a));
      }),
  }),
  subject: yup.string().when("via", {
    is: "email",
    then: (s) => s.trim().required("Add a subject.").max(200),
  }),
  channel: yup.string().when("via", {
    is: "slack",
    then: (s) => s.trim().required("Enter a Slack channel."),
  }),
  body: yup.string().trim().required("Write a message."),
});

const replySchema = yup.object({ body: yup.string().trim().required() });

function StartForm({ ticketId, close }: { ticketId: string; close: () => void }) {
  const email = useStartSideEmail(ticketId);
  const slack = useStartSideSlack(ticketId);
  const [error, setError] = useState<string | null>(null);
  const pending = email.isPending || slack.isPending;

  const form = useFormik({
    initialValues: { via: "email", to: "", subject: "", channel: "", body: "" },
    validationSchema: startSchema,
    onSubmit: (v) => {
      setError(null);
      const onError = (e: unknown) => {
        if (v.via === "slack" && e instanceof ApiError) {
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
      if (v.via === "email")
        email.mutate(
          { to: splitEmails(v.to), subject: v.subject.trim(), body: v.body.trim() },
          { onSuccess, onError },
        );
      else slack.mutate({ channel: v.channel.trim(), body: v.body.trim() }, { onSuccess, onError });
    },
  });
  const via = form.values.via;
  // One banner, like before: the first problem in field order.
  const problem =
    error ??
    (via === "email"
      ? (fieldError(form, "to") ?? fieldError(form, "subject"))
      : fieldError(form, "channel")) ??
    fieldError(form, "body") ??
    null;

  return (
    <form className="space-y-2" onSubmit={form.handleSubmit} noValidate>
      <Select {...bindSelect(form, "via")} aria-label="Start by">
        <option value="email">By email</option>
        <option value="slack">In Slack</option>
      </Select>
      {via === "email" ? (
        <>
          <Input
            {...bind(form, "to")}
            placeholder="To (comma-separated emails)"
            aria-label="To"
            className="h-9"
          />
          <Input
            {...bind(form, "subject")}
            placeholder="Subject"
            aria-label="Subject"
            maxLength={200}
            className="h-9"
          />
        </>
      ) : (
        <Input
          {...bind(form, "channel")}
          placeholder="#channel"
          aria-label="Slack channel"
          className="h-9"
        />
      )}
      <Textarea
        {...bind(form, "body")}
        placeholder="Message"
        aria-label="Message"
        className="min-h-20"
      />
      {problem && (
        <p role="alert" className="text-sm text-danger">
          {problem}
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
  const replyForm = useFormik({
    initialValues: { body: "" },
    validationSchema: replySchema,
    onSubmit: (v, { resetForm }) =>
      reply.mutate(
        { side_id: s.id, body: v.body.trim() },
        { onSuccess: () => resetForm(), onError: toastError },
      ),
  });
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
        <form className="mt-2 flex gap-2" onSubmit={replyForm.handleSubmit} noValidate>
          <Input
            {...bind(replyForm, "body")}
            placeholder="Write back"
            aria-label="Write back"
            className="h-9"
          />
          <Button
            type="submit"
            size="sm"
            className="h-9"
            loading={reply.isPending}
            disabled={!replyForm.values.body.trim()}
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

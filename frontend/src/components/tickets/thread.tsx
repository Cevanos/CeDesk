"use client";

import {
  AlertCircle,
  AtSign,
  Check,
  CheckCircle2,
  Loader2,
  Lock,
  Maximize2,
  Minimize2,
  Paperclip,
  Send,
  Sparkles,
  X,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { ComposerAi, TranslateMessage } from "@/components/tickets/ai-assist";
import { FileChip, formatBytes } from "@/components/tickets/message-files";
import { Badge, Button, Card, Select } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { useEscape } from "@/hooks/use-escape";
import {
  useAcceptTicket,
  useOlderMessages,
  useSendMessage,
  useUploadAttachment,
} from "@/hooks/use-tickets";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import { useAgents, useSavedReplies } from "@/hooks/use-workspace";
import type { Delivery, Message, SavedReply, Ticket } from "@/lib/api/types";
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

const MAX_FILES = 10;
const MAX_FILE_BYTES = 25 * 1024 * 1024;
/** Email replies: files may total at most 18 MB. */
const MAX_EMAIL_REPLY_BYTES = 18 * 1024 * 1024;

interface Pending {
  id: string;
  name: string;
  size: number;
}

/** Text after a `/` that starts a word, just before the caret: `/ref` in "hi /ref". */
const SLASH = /(^|\s)\/(\S*)$/;

function SavedReplyList({
  items,
  active,
  onPick,
  onHover,
}: {
  items: SavedReply[];
  active: number;
  onPick: (r: SavedReply) => void;
  onHover: (i: number) => void;
}) {
  return (
    <ul
      id="saved-replies-list"
      role="listbox"
      aria-label="Saved replies"
      className="absolute right-0 bottom-[calc(100%+8px)] left-0 z-50 max-h-64 animate-rise overflow-y-auto rounded-xl border border-border bg-surface p-1.5 shadow-pop"
    >
      {items.map((r, i) => (
        <li
          key={r.id}
          id={`saved-reply-${i}`}
          role="option"
          aria-selected={i === active}
          onMouseDown={(e) => {
            e.preventDefault(); // keep focus in the textarea
            onPick(r);
          }}
          onMouseEnter={() => onHover(i)}
          className={cn(
            "cursor-pointer rounded-lg px-2.5 py-2",
            i === active ? "bg-primary-soft" : "hover:bg-surface-2",
          )}
        >
          <div className="flex items-center gap-1.5 text-sm font-medium text-foreground-2">
            <Zap className="size-3.5 text-primary" aria-hidden /> /{r.shortcut}
          </div>
          <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{r.body}</p>
        </li>
      ))}
    </ul>
  );
}

export function Composer({
  ticketId,
  canReply,
  accepted = true,
  channel = null,
}: {
  ticketId: string;
  canReply: boolean;
  /** False on a handover ticket nobody has accepted yet: replies are refused (409). */
  accepted?: boolean;
  channel?: string | null;
}) {
  const send = useSendMessage(ticketId);
  const upload = useUploadAttachment(ticketId);
  const agents = useAgents();
  const canReplyNow = canReply && accepted;
  const [picked, setKind] = useState<"reply" | "note" | null>(null);
  // Reply by default; a handover that is not accepted only allows notes.
  const kind = canReplyNow ? (picked ?? "reply") : "note";
  const [body, setBody] = useState("");
  const [files, setFiles] = useState<Pending[]>([]);
  const [uploading, setUploading] = useState(0);
  // A half-written reply is lost on reload or tab close: ask first.
  useUnsavedChanges(body.trim().length > 0 || files.length > 0);
  const [mentions, setMentions] = useState<{ id: string; name: string }[]>([]);

  const isChat = (channel ?? "").toLowerCase() === "chat";
  const filesAllowed = !(kind === "reply" && isChat);
  const fileInput = useRef<HTMLInputElement>(null);

  const addFiles = async (list: File[]) => {
    let count = files.length;
    let bytes = files.reduce((n, f) => n + f.size, 0);
    for (const f of list) {
      if (count >= MAX_FILES) {
        toast.error(`You can attach up to ${MAX_FILES} files.`);
        break;
      }
      if (f.size > MAX_FILE_BYTES) {
        toast.error(`${f.name} is larger than 25 MB.`);
        continue;
      }
      if (kind === "reply" && channel === "email" && bytes + f.size > MAX_EMAIL_REPLY_BYTES) {
        toast.error("Files on an email reply can total at most 18 MB.");
        continue;
      }
      count += 1;
      bytes += f.size;
      setUploading((n) => n + 1);
      try {
        const a = await upload.mutateAsync(f);
        setFiles((x) => [...x, { id: a.id, name: a.name ?? f.name, size: a.size ?? f.size }]);
      } catch {
        count -= 1;
        bytes -= f.size;
      } finally {
        setUploading((n) => n - 1);
      }
    }
  };

  const [caret, setCaret] = useState(0);
  const [closed, setClosed] = useState(false);
  const [hi, setHi] = useState(0);
  const slash = kind === "reply" ? SLASH.exec(body.slice(0, caret)) : null;
  const slashQuery = slash?.[2] ?? "";
  // Shortcuts are at most 33 characters: anything longer is a path or URL, not a lookup.
  const lookup = !!slash && !closed && slashQuery.length <= 33;
  const replies = useSavedReplies(slashQuery, lookup);
  const matches = lookup ? (replies.data ?? []).slice(0, 8) : [];
  const listOpen = matches.length > 0;

  const composerRef = useRef<HTMLTextAreaElement>(null);
  const grow = (el: HTMLTextAreaElement, isFull: boolean) => {
    if (isFull) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };

  const pick = (r: SavedReply) => {
    const start = caret - 1 - slashQuery.length;
    const next = body.slice(0, start) + r.body + body.slice(caret);
    const pos = start + r.body.length;
    setBody(next);
    setCaret(pos);
    setHi(0);
    requestAnimationFrame(() => {
      const el = composerRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(pos, pos);
      grow(el, false);
    });
  };

  // Files can't go on chat replies (422): switching to Reply with files attached blocks sending.
  const filesBlocked = kind === "reply" && isChat && files.length > 0;

  const submit = () => {
    const text = body.trim();
    if ((!text && files.length === 0) || uploading > 0 || filesBlocked) return;
    send.mutate(
      {
        kind,
        body: text,
        ...(kind === "note" && mentions.length ? { mention_ids: mentions.map((m) => m.id) } : {}),
        ...(files.length ? { attachment_ids: files.map((f) => f.id) } : {}),
      },
      {
        onSuccess: () => {
          setBody("");
          setFiles([]);
          setMentions([]);
          if (composerRef.current) composerRef.current.style.height = "auto";
          toast.success(kind === "reply" ? "Reply sent" : "Note added");
        },
      },
    );
  };

  const people = (agents.data ?? []).filter((a) => !mentions.some((m) => m.id === a.member_id));

  const [full, setFull] = useState(false);
  useEscape(full, () => setFull(false));
  useEffect(() => {
    if (full) composerRef.current?.focus();
  }, [full]);
  const [picking, setPicking] = useState(false);
  const isNote = kind === "note";
  const tab = (active: boolean, note?: boolean) =>
    cn(
      "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[13px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50",
      active
        ? note
          ? "bg-warning-soft text-warning ring-1 ring-note-border"
          : "bg-primary-soft text-primary-ink ring-1 ring-primary/20"
        : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
    );

  const bar = (
    <div
      className={cn(
        "border p-2 shadow-pop backdrop-blur transition-colors",
        full ? "flex h-full w-full max-w-4xl flex-col rounded-2xl p-4" : "rounded-2xl",
        isNote ? "border-note-border bg-note" : "border-border-strong bg-surface",
      )}
    >
      <div className="flex items-center gap-1 px-1 pt-0.5 pb-1.5">
        {/* Only the tabs live inside the tablist; the expand button is a sibling. */}
        <div role="tablist" aria-label="Message type" className="flex items-center gap-1">
          {canReply && (
            <button
              role="tab"
              aria-selected={kind === "reply"}
              disabled={!accepted}
              title={accepted ? undefined : "Accept the chat first to reply"}
              onClick={() => setKind("reply")}
              className={tab(kind === "reply")}
            >
              <Send className="size-3.5" /> Reply
            </button>
          )}
          <button
            role="tab"
            aria-selected={isNote}
            onClick={() => setKind("note")}
            className={tab(isNote, true)}
          >
            <Lock className="size-3.5" /> Private note
          </button>
        </div>
        {isNote && (
          <span className="ml-1 hidden text-xs text-warning sm:inline">
            Only your team can see this
          </span>
        )}
        {canReply && !accepted && (
          <span className="ml-1 hidden text-xs text-muted-foreground sm:inline">
            Accept the chat to reply
          </span>
        )}
        <button
          type="button"
          onClick={() => setFull((f) => !f)}
          aria-label={full ? "Exit full screen" : "Expand to full screen"}
          title={full ? "Exit full screen" : "Expand to full screen"}
          className="ml-auto grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-surface-3 hover:text-foreground"
        >
          {full ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
        </button>
      </div>
      {isNote && mentions.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-1 pb-1.5">
          {mentions.map((m) => (
            <Badge key={m.id} tone="primary" className="gap-1 pr-1">
              @{m.name}
              <button
                aria-label={`Remove ${m.name}`}
                onClick={() => setMentions((x) => x.filter((y) => y.id !== m.id))}
              >
                <X className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}
      <ComposerAi
        ticketId={ticketId}
        body={body}
        setBody={setBody}
        canSuggest={canReplyNow && kind === "reply"}
      />
      {(files.length > 0 || uploading > 0) && (
        <ul className="flex flex-wrap gap-1.5 px-1 pb-1.5" aria-label="Files to send">
          {files.map((f) => (
            <li key={f.id}>
              <Badge tone="primary" className="max-w-56 gap-1 py-1 pr-1">
                <Paperclip className="size-3 flex-none" aria-hidden />
                <span className="truncate">{f.name}</span>
                <span className="flex-none font-normal opacity-70">{formatBytes(f.size)}</span>
                <button
                  type="button"
                  aria-label={`Remove ${f.name}`}
                  onClick={() => setFiles((x) => x.filter((y) => y.id !== f.id))}
                >
                  <X className="size-3" />
                </button>
              </Badge>
            </li>
          ))}
          {uploading > 0 && (
            <li>
              <Badge className="gap-1 py-1">
                <Loader2 className="size-3 animate-spin" aria-hidden /> Uploading…
              </Badge>
            </li>
          )}
        </ul>
      )}
      <div
        className={cn(
          "relative flex gap-2 rounded-xl border px-2 py-1.5",
          full ? "min-h-0 flex-1 items-stretch" : "items-end",
          isNote ? "border-note-border bg-surface/70" : "border-input bg-surface-2/60",
        )}
      >
        {listOpen && <SavedReplyList items={matches} active={hi} onPick={pick} onHover={setHi} />}
        {isNote && people.length > 0 && (
          <div className="relative mb-1 flex-none">
            <button
              type="button"
              aria-label="Mention a teammate"
              aria-expanded={picking}
              title="Mention a teammate"
              onClick={() => setPicking((v) => !v)}
              className="grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-surface-3"
            >
              <AtSign className="size-4" />
            </button>
            {picking && (
              <div className="absolute bottom-[calc(100%+8px)] left-0 z-50 w-[min(16rem,calc(100vw-2rem))] animate-rise rounded-xl border border-border bg-surface p-2 shadow-pop">
                <Select
                  aria-label="Mention a teammate"
                  placeholder="Search teammates…"
                  defaultOpen
                  value=""
                  options={people.map((p) => ({ value: p.member_id, label: p.name }))}
                  onBlur={() => setPicking(false)}
                  onChange={(e) => {
                    const a = people.find((p) => p.member_id === e.target.value);
                    if (a) {
                      setMentions((m) => [...m, { id: a.member_id, name: a.name }]);
                      setBody((b) => `${b}${b && !b.endsWith(" ") ? " " : ""}@${a.name} `);
                    }
                    setPicking(false);
                  }}
                />
              </div>
            )}
          </div>
        )}
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          aria-hidden
          tabIndex={-1}
          onChange={(e) => {
            const list = Array.from(e.target.files ?? []);
            e.target.value = "";
            if (list.length) void addFiles(list);
          }}
        />
        {filesAllowed ? (
          <button
            type="button"
            aria-label="Attach files"
            title="Attach files"
            onClick={() => fileInput.current?.click()}
            className="mb-1 grid size-8 flex-none place-items-center rounded-lg text-muted-foreground hover:bg-surface-3 hover:text-foreground"
          >
            <Paperclip className="size-4" />
          </button>
        ) : (
          <span
            className="mb-1 grid size-8 flex-none place-items-center text-subtle-foreground"
            title="Files can't be sent on chat replies. Add a private note instead."
          >
            <Paperclip className="size-4 opacity-40" aria-hidden />
          </span>
        )}
        <textarea
          value={body}
          rows={1}
          ref={composerRef}
          onChange={(e) => {
            setBody(e.target.value);
            setCaret(e.target.selectionStart);
            setClosed(false);
            setHi(0);
            grow(e.target, full);
          }}
          onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
          onKeyDown={(e) => {
            if (listOpen) {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                return setHi((i) => (i + 1) % matches.length);
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                return setHi((i) => (i - 1 + matches.length) % matches.length);
              }
              if ((e.key === "Enter" && !e.metaKey && !e.ctrlKey) || e.key === "Tab") {
                e.preventDefault();
                const choice = matches[Math.min(hi, matches.length - 1)];
                if (choice) pick(choice);
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                return setClosed(true);
              }
            }
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit();
          }}
          role={kind === "reply" ? "combobox" : undefined}
          aria-autocomplete={kind === "reply" ? "list" : undefined}
          aria-expanded={kind === "reply" ? listOpen : undefined}
          aria-controls={listOpen ? "saved-replies-list" : undefined}
          aria-activedescendant={listOpen ? `saved-reply-${hi}` : undefined}
          aria-label={isNote ? "Private note" : "Reply to the customer"}
          placeholder={
            isNote
              ? "Write a private note for your team…"
              : "Write a reply… type / for saved replies"
          }
          maxLength={50000}
          className={cn(
            "min-h-9 flex-1 resize-none bg-transparent px-1.5 py-2 text-sm leading-5 text-foreground outline-none placeholder:text-subtle-foreground",
            full ? "h-full text-[15px] leading-6" : "max-h-40",
          )}
        />
        <Button
          size="icon"
          className={cn("mb-0.5 size-9 rounded-full", full && "mt-auto")}
          onClick={submit}
          loading={send.isPending}
          disabled={(!body.trim() && files.length === 0) || uploading > 0 || filesBlocked}
          aria-label={isNote ? "Add note" : "Send reply"}
        >
          {!send.isPending && <Send className="size-4" />}
        </Button>
      </div>
      <p className="hidden px-2 pt-1 text-[11px] text-subtle-foreground sm:block">
        Ctrl/⌘ + Enter to send
        {isChat && kind === "reply" && " · files aren't supported on chat replies"}
        {filesBlocked && " · remove the files or switch to a private note"}
      </p>
    </div>
  );

  return full ? (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={isNote ? "Add private note" : "Write reply"}
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-3 backdrop-blur-sm sm:p-8"
    >
      {bar}
    </div>
  ) : (
    bar
  );
}

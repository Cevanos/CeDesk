"use client";

import { AtSign, Loader2, Lock, Maximize2, Minimize2, Paperclip, Send, X, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { ComposerAi } from "@/components/tickets/ai-assist";
import { formatBytes } from "@/components/tickets/message-files";
import { Badge, Button, Select } from "@/components/ui/primitives";
import { useEscape } from "@/hooks/use-escape";
import { useSendMessage, useUploadAttachment } from "@/hooks/use-tickets";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import { useAgents, useSavedReplies } from "@/hooks/use-workspace";
import type { SavedReply } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export const MAX_FILES = 10;
export const MAX_FILE_BYTES = 25 * 1024 * 1024;
/** Email replies: files may total at most 18 MB. */
export const MAX_EMAIL_REPLY_BYTES = 18 * 1024 * 1024;

export interface Pending {
  id: string;
  name: string;
  size: number;
}

/** Text after a `/` that starts a word, just before the caret: `/ref` in "hi /ref". */
export const SLASH = /(^|\s)\/(\S*)$/;

export function SavedReplyList({
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
  replyCc = [],
}: {
  ticketId: string;
  canReply: boolean;
  /** False on a handover ticket nobody has accepted yet: replies are refused (409). */
  accepted?: boolean;
  channel?: string | null;
  /** Who the reply will copy, shown read-only above the text box. */
  replyCc?: string[];
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

  const people = (agents.data ?? []).filter(
    (a) => a.is_active !== false && !mentions.some((m) => m.id === a.member_id),
  );

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
      {kind === "reply" && replyCc.length > 0 && (
        <p className="px-2 pb-1.5 text-xs text-muted-foreground" data-testid="reply-cc">
          <span className="font-medium">Cc:</span> {replyCc.join(", ")}
        </p>
      )}
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

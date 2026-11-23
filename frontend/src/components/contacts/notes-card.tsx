"use client";

import { Pencil, StickyNote, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { InfiniteScroll } from "@/components/ui/infinite-scroll";
import { Button, Card, Textarea } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { useAddNote, useAllNotes, useDeleteNote, useEditNote } from "@/hooks/use-contacts";
import type { ContactDetail, ContactNote } from "@/lib/api/contacts-types";
import { hasAction } from "@/lib/permissions";
import { cn, formatDateTime } from "@/lib/utils";

const MAX = 5000;

/** Length as the API counts it: characters of the trimmed text, not UTF-16 units. */
const noteLength = (text: string) => [...text.trim()].length;

/** Length counter that turns into an error once a note is too long to save. */
function Counter({ length }: { length: number }) {
  const over = length > MAX;
  return (
    <p
      role={over ? "alert" : undefined}
      className={over ? "text-xs font-medium text-danger" : "text-xs text-muted-foreground"}
    >
      {over
        ? `This note is ${length - MAX} characters too long. Notes can be at most ${MAX} characters.`
        : `${length}/${MAX}`}
    </p>
  );
}

function NoteItem({
  contactId,
  note,
  locked,
}: {
  contactId: string;
  note: ContactNote;
  locked: boolean;
}) {
  const edit = useEditNote(contactId);
  const del = useDeleteNote(contactId);
  const { ask, dialog } = useConfirm();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(note.body);
  const edited = note.updated_at !== note.created_at;
  const canEdit = note.can_edit && !locked;

  return (
    <li className="py-3 first:pt-0 last:pb-0">
      {dialog}
      <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
        <span className="font-medium text-foreground-2">{note.author_name}</span>
        <span>
          {formatDateTime(note.created_at)}
          {edited && " · edited"}
        </span>
        {canEdit && !editing && (
          <span className="ml-auto flex gap-1">
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2"
              onClick={() => {
                setDraft(note.body);
                setEditing(true);
              }}
            >
              <Pencil className="size-3" /> Edit
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2"
              onClick={() =>
                ask({
                  title: "Delete this note?",
                  message: "The note is removed for everyone.",
                  confirmLabel: "Delete",
                  run: () =>
                    del.mutateAsync(note.id).then(() => {
                      toast.success("Note deleted");
                    }),
                })
              }
            >
              <Trash2 className="size-3" /> Delete
            </Button>
          </span>
        )}
      </div>
      {editing ? (
        <form
          className="mt-2 space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            const body = draft.trim();
            if (!body || noteLength(body) > MAX) return;
            edit.mutate({ noteId: note.id, body }, { onSuccess: () => setEditing(false) });
          }}
        >
          <Textarea
            aria-label="Edit note"
            rows={3}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <Counter length={noteLength(draft)} />
          <div className="flex justify-end gap-2">
            <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              size="sm"
              loading={edit.isPending}
              disabled={!draft.trim() || noteLength(draft) > MAX}
            >
              Save
            </Button>
          </div>
        </form>
      ) : (
        <p className="mt-1 text-sm break-words whitespace-pre-wrap">{note.body}</p>
      )}
    </li>
  );
}

/** Private notes on a customer. Only the team sees them. */
export function NotesCard({
  contact,
  compact,
  className,
}: {
  contact: ContactDetail;
  compact?: boolean;
  className?: string;
}) {
  const { user } = useCurrentUser();
  const canAdd = hasAction(user, "private_note") && !contact.erased_at;
  const add = useAddNote(contact.id);
  const [text, setText] = useState("");
  const [all, setAll] = useState(false);
  const full = useAllNotes(contact.id, all);

  const notes = all && full.data ? full.data.pages.flatMap((p) => p.items) : contact.notes;
  const more = contact.note_count > contact.notes.length;

  return (
    <Card className={cn("rounded-2xl p-4 sm:p-5", className)}>
      <h3 className="mb-3 flex items-center gap-2 text-[11px] font-bold tracking-[0.08em] text-muted-foreground uppercase after:h-px after:flex-1 after:bg-border">
        <StickyNote className="size-3.5" /> Notes
        {contact.note_count > 0 && (
          <span className="rounded-full bg-surface-3 px-1.5 py-0.5 text-[10px] font-medium">
            {contact.note_count}
          </span>
        )}
      </h3>

      {canAdd && (
        <form
          className="mb-3 space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            const body = text.trim();
            if (!body || noteLength(body) > MAX) return;
            add.mutate(body, {
              onSuccess: () => {
                setText("");
                toast.success("Note saved");
              },
            });
          }}
        >
          <Textarea
            aria-label="Add a private note"
            placeholder="Add a private note"
            rows={compact ? 2 : 3}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          {text.length > 0 && <Counter length={noteLength(text)} />}
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">Only your team sees notes.</p>
            <Button
              type="submit"
              size="sm"
              loading={add.isPending}
              disabled={!text.trim() || noteLength(text) > MAX}
            >
              Save
            </Button>
          </div>
        </form>
      )}

      {notes.length === 0 ? (
        <p className="text-sm text-muted-foreground">No notes yet.</p>
      ) : (
        <ul className="divide-y divide-border">
          {notes.map((n) => (
            <NoteItem key={n.id} contactId={contact.id} note={n} locked={!!contact.erased_at} />
          ))}
        </ul>
      )}

      {!all && more && (
        <Button size="sm" variant="ghost" className="mt-2" onClick={() => setAll(true)}>
          Show all notes
        </Button>
      )}
      {all && <InfiniteScroll query={full} shown={notes.length} noun="notes" />}
    </Card>
  );
}

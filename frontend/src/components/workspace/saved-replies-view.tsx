"use client";

import { Pencil, Plus, Trash2, Zap } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Dialog } from "@/components/ui/overlay";
import {
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Skeleton,
  Textarea,
} from "@/components/ui/primitives";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import { useDeleteSavedReply, useSavedReplies, useSaveSavedReply } from "@/hooks/use-workspace";
import type { SavedReply } from "@/lib/api/types";

const PLACEHOLDERS = ["{{customer.first_name}}", "{{customer.name}}", "{{agent.name}}"];

export function SavedRepliesView() {
  const replies = useSavedReplies("");
  const del = useDeleteSavedReply();
  const [editing, setEditing] = useState<SavedReply | "new" | null>(null);

  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Agents type <kbd className="rounded bg-surface-2 px-1">/</kbd> in the reply box to insert
          one. Placeholders are filled in when the reply is sent.
        </p>
        <Button onClick={() => setEditing("new")}>
          <Plus className="size-4" /> Add saved reply
        </Button>
      </div>

      {replies.isPending ? (
        <Skeleton className="h-32" />
      ) : replies.isError ? (
        <LoadError error={replies.error} />
      ) : replies.data.length === 0 ? (
        <Card>
          <EmptyState icon={<Zap className="size-6" />} title="No saved replies yet">
            Add answers you send often, like a refund policy or a shipping update.
          </EmptyState>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-border">
            {replies.data.map((r) => (
              <li key={r.id} className="flex flex-wrap items-start gap-3 px-4 py-3">
                <div className="min-w-0 flex-1 basis-60">
                  <div className="font-mono text-sm font-semibold">/{r.shortcut}</div>
                  <p className="mt-0.5 line-clamp-2 text-sm whitespace-pre-wrap text-muted-foreground">
                    {r.body}
                  </p>
                </div>
                <Button size="sm" variant="outline" onClick={() => setEditing(r)}>
                  <Pencil className="size-3.5" /> Edit
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={del.isPending}
                  onClick={() => {
                    if (confirm(`Delete saved reply “/${r.shortcut}”?`))
                      del.mutate(r.id, { onSuccess: () => toast.success("Saved reply deleted") });
                  }}
                >
                  <Trash2 className="size-3.5" /> Delete
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "Add saved reply" : "Edit saved reply"}
      >
        {editing !== null && (
          <ReplyForm
            key={editing === "new" ? "new" : editing.id}
            reply={editing === "new" ? null : editing}
            onClose={() => setEditing(null)}
          />
        )}
      </Dialog>
    </div>
  );
}

function ReplyForm({ reply, onClose }: { reply: SavedReply | null; onClose: () => void }) {
  const save = useSaveSavedReply();
  const [shortcut, setShortcut] = useState(reply?.shortcut ?? "");
  const [body, setBody] = useState(reply?.body ?? "");
  const [errs, setErrs] = useState<Record<string, string>>({});

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const sc = shortcut.trim().replace(/^\/+/, "");
        const next: Record<string, string> = {};
        if (!sc) next.shortcut = "Enter a shortcut";
        else if (/\s/.test(sc)) next.shortcut = "No spaces in a shortcut";
        if (!body.trim()) next.body = "Enter the reply text";
        setErrs(next);
        if (Object.keys(next).length) return;
        save.mutate(
          { id: reply?.id, shortcut: sc, body },
          {
            onSuccess: () => {
              toast.success("Saved reply saved");
              onClose();
            },
            onError: (err) => setErrs(fieldErrors(err)),
          },
        );
      }}
    >
      <Field
        label="Shortcut"
        htmlFor="sr-shortcut"
        error={errs.shortcut}
        hint="Agents type /shortcut to find it."
      >
        <Input
          id="sr-shortcut"
          value={shortcut}
          onChange={(e) => setShortcut(e.target.value)}
          placeholder="refund-policy"
          maxLength={33}
          autoCapitalize="none"
          spellCheck={false}
        />
      </Field>
      <Field
        label="Reply"
        htmlFor="sr-body"
        error={errs.body}
        hint={`Placeholders: ${PLACEHOLDERS.join(", ")}, {{fields.<key>}}`}
      >
        <Textarea
          id="sr-body"
          rows={7}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          maxLength={10000}
        />
      </Field>
      <FormError message={errs._form} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          Save
        </Button>
      </div>
    </form>
  );
}

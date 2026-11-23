"use client";

import { Filter, MoreHorizontal, Pencil, Trash2, Users } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { Dialog, MenuItem, Popover } from "@/components/ui/overlay";
import { Button, Card, Field, Input } from "@/components/ui/primitives";
import { FormError } from "@/components/workspace/list-shared";
import { useDeleteSegment, useSaveSegment, useSegments } from "@/hooks/use-contacts";
import { ApiError } from "@/lib/api/client";
import type { ContactFieldRef, ContactFilters, Segment } from "@/lib/api/contacts-types";
import { filtersToSegment } from "@/lib/contacts";
import { cn } from "@/lib/utils";

const msg = (e: unknown) => (e instanceof ApiError ? e.userMessage : "Something went wrong.");

/** Name a segment (new or renamed). A new one saves the list's current filters. */
export function SegmentNameDialog({
  open,
  onClose,
  segment,
  filters,
  fields,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  /** Set to rename; leave out to save the current filters as a new segment. */
  segment?: Segment;
  filters?: ContactFilters;
  fields?: ContactFieldRef[];
  onSaved?: (s: Segment) => void;
}) {
  return (
    <Dialog open={open} onClose={onClose} title={segment ? "Rename segment" : "Save as segment"}>
      {open && (
        <NameForm
          segment={segment}
          filters={filters}
          fields={fields ?? []}
          onClose={onClose}
          onSaved={onSaved}
        />
      )}
    </Dialog>
  );
}

function NameForm({
  segment,
  filters,
  fields,
  onClose,
  onSaved,
}: {
  segment?: Segment;
  filters?: ContactFilters;
  fields: ContactFieldRef[];
  onClose: () => void;
  onSaved?: (s: Segment) => void;
}) {
  const save = useSaveSegment();
  const [name, setName] = useState(segment?.name ?? "");
  const trimmed = name.trim();
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (!trimmed) return;
        save.mutate(
          segment
            ? { id: segment.id, name: trimmed }
            : { name: trimmed, filters: filtersToSegment(filters!, fields) },
          {
            onSuccess: (s) => {
              toast.success(segment ? "Segment renamed" : "Segment saved");
              onSaved?.(s);
              onClose();
            },
          },
        );
      }}
    >
      <Field label="Name" htmlFor="seg-name" hint="Shared with everyone in the workspace.">
        <Input
          id="seg-name"
          maxLength={80}
          // The dialog has one box, so it takes focus.
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      {save.isError && <FormError message={msg(save.error)} />}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending} disabled={!trimmed}>
          Save
        </Button>
      </div>
    </form>
  );
}

/** The saved filters, shared with the workspace. "All contacts" is the plain list. */
export function SegmentsPanel({
  activeId,
  onSelect,
  canManage,
  onEditFilters,
}: {
  activeId: string | null;
  onSelect: (s: Segment | null) => void;
  canManage: boolean;
  /** "Update filters": the list takes this segment's filters so they can be changed. */
  onEditFilters: (s: Segment) => void;
}) {
  const segments = useSegments();
  const del = useDeleteSegment();
  const { ask, dialog } = useConfirm();
  const [renaming, setRenaming] = useState<Segment | null>(null);

  const item = (active: boolean) =>
    cn(
      "flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition-colors",
      active
        ? "bg-primary/10 font-medium text-primary-ink"
        : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
    );

  return (
    <Card className="rounded-2xl p-2">
      {dialog}
      <h2 className="px-2.5 pt-1.5 pb-2 text-[10.5px] font-semibold tracking-[0.12em] text-subtle-foreground uppercase">
        Segments
      </h2>
      <ul className="space-y-0.5">
        <li className="flex items-center">
          <button type="button" className={item(activeId === null)} onClick={() => onSelect(null)}>
            <Users className="size-4 shrink-0" /> All contacts
          </button>
        </li>
        {segments.data?.map((s) => (
          <li key={s.id} className="flex items-center">
            <button
              type="button"
              className={item(activeId === s.id)}
              aria-current={activeId === s.id ? "true" : undefined}
              onClick={() => onSelect(s)}
            >
              <Filter className="size-4 shrink-0" />
              <span className="truncate">{s.name}</span>
            </button>
            {canManage && (
              <Popover
                trigger={({ props }) => (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8"
                    aria-label={`Actions for ${s.name}`}
                    {...props}
                  >
                    <MoreHorizontal className="size-4" />
                  </Button>
                )}
              >
                {(close) => (
                  <>
                    <MenuItem
                      role="menuitem"
                      onClick={() => {
                        close();
                        setRenaming(s);
                      }}
                    >
                      <Pencil className="size-4" /> Rename
                    </MenuItem>
                    <MenuItem
                      role="menuitem"
                      onClick={() => {
                        close();
                        onEditFilters(s);
                      }}
                    >
                      <Filter className="size-4" /> Update filters
                    </MenuItem>
                    <MenuItem
                      role="menuitem"
                      className="text-danger"
                      onClick={() => {
                        close();
                        ask({
                          title: "Delete this segment?",
                          message: "Customers are not deleted.",
                          detail: s.name,
                          confirmLabel: "Delete",
                          run: () =>
                            del.mutateAsync(s.id).then(() => {
                              toast.success("Segment deleted");
                              if (activeId === s.id) onSelect(null);
                            }),
                        });
                      }}
                    >
                      <Trash2 className="size-4" /> Delete
                    </MenuItem>
                  </>
                )}
              </Popover>
            )}
          </li>
        ))}
        {segments.isError && (
          <li className="px-2.5 py-1 text-xs text-danger">{msg(segments.error)}</li>
        )}
      </ul>
      <SegmentNameDialog
        open={renaming !== null}
        segment={renaming ?? undefined}
        onClose={() => setRenaming(null)}
      />
    </Card>
  );
}

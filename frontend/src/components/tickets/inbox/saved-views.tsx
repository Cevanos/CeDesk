"use client";

import { BookmarkPlus, Bookmark, MoreHorizontal, Users } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { Dialog, MenuItem, Popover } from "@/components/ui/overlay";
import { Button, Field, Input } from "@/components/ui/primitives";
import { FormError } from "@/components/workspace/list-shared";
import { useCurrentUser } from "@/hooks/use-auth";
import { useCreateView, useDeleteView, useUpdateView, useViews } from "@/hooks/use-workspace";
import { apiMessage } from "@/lib/api/messages";
import { filtersToView, type TicketFilters } from "@/lib/api/ticket-filters";
import type { SavedView } from "@/lib/api/types";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";
import { inboxActions } from "@/lib/permissions";
import { cn } from "@/lib/utils";

const NAME_MAX = 80;
const nameSchema = yup.object({
  name: yup.string().trim().required("Give the view a name.").max(NAME_MAX),
});

/** True when a view's saved filters are exactly what the list shows now. */
export function sameFilters(a: Record<string, string>, b: Record<string, string>): boolean {
  // A view saved without a status means the default tab.
  const x: Record<string, string> = { status: "open", ...a };
  const y: Record<string, string> = { status: "open", ...b };
  const kx = Object.keys(x);
  return kx.length === Object.keys(y).length && kx.every((k) => x[k] === y[k]);
}

/**
 * The "Views" strip above the ticket list: shared views first, then the person's own, and a
 * "Save view" button. A view applies its filters to the list as they are.
 */
export function SavedViews({
  filters,
  onApply,
}: {
  filters: TicketFilters;
  onApply: (filters: Record<string, string>) => void;
}) {
  const { user } = useCurrentUser();
  const views = useViews();
  const canShare = inboxActions(user).shareViews;
  const [saving, setSaving] = useState(false);
  const current = filtersToView(filters);

  return (
    <section aria-label="Views" className="mt-4 flex flex-wrap items-center gap-2">
      <span className="inline-flex items-center gap-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        <Bookmark className="size-3.5" aria-hidden /> Views
      </span>

      {views.isPending && <span className="text-xs text-muted-foreground">Loading…</span>}
      {views.isError && <span className="text-xs text-danger">Couldn&rsquo;t load views.</span>}
      {views.data?.length === 0 && (
        <span className="text-xs text-muted-foreground">No saved views yet.</span>
      )}

      {views.data?.map((v) => (
        <ViewChip
          key={v.id}
          view={v}
          active={sameFilters(v.filters, current)}
          canShare={canShare}
          current={current}
          onApply={() => onApply(v.filters)}
        />
      ))}

      <Button variant="outline" size="sm" onClick={() => setSaving(true)}>
        <BookmarkPlus className="size-4" aria-hidden /> Save view
      </Button>

      {saving && (
        <SaveViewDialog filters={current} canShare={canShare} onClose={() => setSaving(false)} />
      )}
    </section>
  );
}

function ViewChip({
  view,
  active,
  canShare,
  current,
  onApply,
}: {
  view: SavedView;
  active: boolean;
  canShare: boolean;
  current: Record<string, string>;
  onApply: () => void;
}) {
  const update = useUpdateView();
  const del = useDeleteView();
  const { ask, dialog } = useConfirm();
  const [renaming, setRenaming] = useState(false);

  const fail = (e: unknown) => toast.error(apiMessage(e));

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border text-sm",
        active
          ? "border-primary/40 bg-primary-soft text-primary-ink"
          : "border-border bg-surface text-foreground-2",
      )}
    >
      {dialog}
      <button
        onClick={onApply}
        aria-pressed={active}
        title={view.shared ? `Shared by ${view.owner_name ?? "a teammate"}` : undefined}
        className={cn(
          "inline-flex max-w-[14rem] items-center gap-1.5 rounded-full py-1 pl-3 text-[13px] font-medium hover:underline",
          view.can_edit ? "pr-1" : "pr-3",
        )}
      >
        {view.shared && <Users className="size-3.5 flex-none" aria-label="Shared" />}
        <span className="truncate">{view.name}</span>
      </button>

      {view.can_edit && (
        <Popover
          align="left"
          className="w-56"
          trigger={({ props }) => (
            <button
              {...props}
              aria-label={`Options for ${view.name}`}
              className="mr-1 grid size-6 place-items-center rounded-full text-muted-foreground hover:bg-surface-2 hover:text-foreground"
            >
              <MoreHorizontal className="size-3.5" />
            </button>
          )}
        >
          {(close) => (
            <>
              <MenuItem
                onClick={() => {
                  close();
                  setRenaming(true);
                }}
              >
                Rename
              </MenuItem>
              <MenuItem
                onClick={() => {
                  close();
                  update.mutate(
                    { id: view.id, filters: current },
                    { onSuccess: () => toast.success("View updated"), onError: fail },
                  );
                }}
              >
                Update filters
                <span className="ml-auto text-xs text-muted-foreground">from the list</span>
              </MenuItem>
              {canShare && (
                <MenuItem
                  onClick={() => {
                    close();
                    update.mutate(
                      { id: view.id, shared: !view.shared },
                      {
                        onSuccess: () =>
                          toast.success(
                            view.shared ? "View is now personal" : "View is now shared",
                          ),
                        onError: fail,
                      },
                    );
                  }}
                >
                  {view.shared ? "Unshare" : "Share with everyone"}
                </MenuItem>
              )}
              <MenuItem
                className="text-danger"
                onClick={() => {
                  close();
                  ask({
                    title: "Delete this view?",
                    message: view.shared
                      ? "It disappears for everyone in the workspace. Tickets are not affected."
                      : "Tickets are not affected.",
                    detail: view.name,
                    confirmLabel: "Delete view",
                    run: () =>
                      del.mutateAsync(view.id).then(
                        () => void toast.success("View deleted"),
                        (e) => {
                          fail(e);
                        },
                      ),
                  });
                }}
              >
                Delete
              </MenuItem>
            </>
          )}
        </Popover>
      )}

      {renaming && <RenameDialog view={view} onClose={() => setRenaming(false)} />}
    </span>
  );
}

function SaveViewDialog({
  filters,
  canShare,
  onClose,
}: {
  filters: Record<string, string>;
  canShare: boolean;
  onClose: () => void;
}) {
  const create = useCreateView();
  const [apiError, setError] = useState<string>();
  const form = useFormik({
    initialValues: { name: "", shared: false },
    validationSchema: nameSchema,
    onSubmit: (v) => {
      const n = v.name.trim();
      setError(undefined);
      create.mutate(
        { name: n, filters, shared: canShare && v.shared },
        {
          onSuccess: () => {
            toast.success(`Saved “${n}”`);
            onClose();
          },
          onError: (err) => setError(apiMessage(err)),
        },
      );
    },
  });
  const error = apiError ?? fieldError(form, "name");

  return (
    <Dialog
      open
      onClose={onClose}
      title="Save view"
      description="Saves the filters, tab and sort you have on now."
    >
      <form noValidate className="space-y-3" onSubmit={form.handleSubmit}>
        <Field label="Name" htmlFor="view-name">
          <Input
            id="view-name"
            {...bind(form, "name")}
            maxLength={NAME_MAX}
            placeholder="My pending tickets"
          />
        </Field>
        {canShare && (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              name="shared"
              checked={form.values.shared}
              onChange={form.handleChange}
              className="size-4 accent-[var(--primary)]"
            />
            Share with everyone
          </label>
        )}
        <FormError message={error} />
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={create.isPending}>
            Save view
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function RenameDialog({ view, onClose }: { view: SavedView; onClose: () => void }) {
  const update = useUpdateView();
  const [apiError, setError] = useState<string>();
  const form = useFormik({
    initialValues: { name: view.name },
    validationSchema: nameSchema,
    onSubmit: (v) => {
      const n = v.name.trim();
      if (n === view.name) return onClose();
      setError(undefined);
      update.mutate(
        { id: view.id, name: n },
        { onSuccess: onClose, onError: (err) => setError(apiMessage(err)) },
      );
    },
  });
  const error = apiError ?? fieldError(form, "name");

  return (
    <Dialog open onClose={onClose} title="Rename view">
      <form noValidate className="space-y-3" onSubmit={form.handleSubmit}>
        <Field label="Name" htmlFor="view-rename">
          <Input id="view-rename" {...bind(form, "name")} maxLength={NAME_MAX} />
        </Field>
        <FormError message={error} />
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={update.isPending}>
            Rename
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

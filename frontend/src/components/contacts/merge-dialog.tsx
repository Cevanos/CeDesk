"use client";

import { Search, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Dialog } from "@/components/ui/overlay";
import { Button, Input, Spinner } from "@/components/ui/primitives";
import { FormError } from "@/components/workspace/list-shared";
import { useContactSearch, useDebounced, useMerge, useMergePreview } from "@/hooks/use-contacts";
import { ApiError } from "@/lib/api/client";
import type { ContactRow, MergeMoves, MergePreview } from "@/lib/api/contacts-types";
import { useFormik, yup } from "@/lib/forms";

const MOVE_LABEL: Record<keyof MergeMoves, [string, string]> = {
  tickets: ["ticket", "tickets"],
  archived_tickets: ["archived ticket", "archived tickets"],
  ratings: ["rating", "ratings"],
  notes: ["note", "notes"],
  other_emails: ["email address", "email addresses"],
  portal_sessions: ["portal sign-in", "portal sign-ins"],
  privacy_requests: ["privacy request", "privacy requests"],
};

export function movesText(moves: MergeMoves): string {
  const parts = (Object.keys(MOVE_LABEL) as (keyof MergeMoves)[])
    .filter((k) => (moves[k] ?? 0) > 0)
    .map((k) => `${moves[k]} ${MOVE_LABEL[k][moves[k] === 1 ? 0 : 1]}`);
  return parts.length ? parts.join(", ") : "nothing";
}

const show = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));
const msg = (e: unknown) => (e instanceof ApiError ? e.userMessage : "Something went wrong.");

/**
 * Merge a duplicate into the customer being viewed (the survivor): pick it, check the
 * preview, tick the box. Nothing changes until the last click.
 */
export function MergeDialog({
  survivorId,
  survivorName,
  open,
  onClose,
}: {
  survivorId: string;
  survivorName: string;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Merge a duplicate into this customer"
      description={`${survivorName} keeps its name and email. The duplicate is deleted after its tickets, notes, ratings and addresses move over.`}
      className="sm:max-w-xl"
    >
      {open && <MergeSteps survivorId={survivorId} survivorName={survivorName} onClose={onClose} />}
    </Dialog>
  );
}

function MergeSteps({
  survivorId,
  survivorName,
  onClose,
}: {
  survivorId: string;
  survivorName: string;
  onClose: () => void;
}) {
  const formik = useFormik({
    initialValues: { search: "", sure: false },
    validationSchema: yup.object({
      search: yup.string().trim().min(2, "Type at least 2 characters."),
      sure: yup.boolean().oneOf([true], "Confirm that you understand this can't be undone."),
    }),
    onSubmit: () => {},
  });
  const q = useDebounced(formik.values.search.trim());
  const found = useContactSearch(q);
  const preview = useMergePreview(survivorId);
  const merge = useMerge(survivorId);
  const [picked, setPicked] = useState<ContactRow | null>(null);
  const [plan, setPlan] = useState<MergePreview | null>(null);

  const choose = (c: ContactRow) => {
    setPicked(c);
    setPlan(null);
    void formik.setFieldValue("sure", false);
    preview.mutate(c.id, { onSuccess: setPlan });
  };

  // Step 1: pick the duplicate.
  if (!picked) {
    const rows = (found.data ?? []).filter((c) => c.id !== survivorId);
    return (
      <div className="space-y-3">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-subtle-foreground" />
          <Input
            aria-label="Find the duplicate"
            placeholder="Search by name, email, phone or organization"
            className="pl-9"
            // The dialog exists to search, so the box takes focus.
            // eslint-disable-next-line jsx-a11y/no-autofocus
            autoFocus
            name="search"
            value={formik.values.search}
            onChange={formik.handleChange}
            onBlur={formik.handleBlur}
          />
        </div>
        {q.length < 2 ? (
          <p className="text-sm text-muted-foreground">Type at least 2 characters.</p>
        ) : found.isPending ? (
          <Spinner />
        ) : found.isError ? (
          <FormError message={msg(found.error)} />
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No other customer matches.</p>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {rows.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => choose(c)}
                  className="flex w-full flex-col px-3 py-2 text-left hover:bg-surface-2"
                >
                  <span className="font-medium">{c.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {c.email} · {c.ticket_count} {c.ticket_count === 1 ? "ticket" : "tickets"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  // Step 2 and 3: the preview, then the confirmation.
  return (
    <div className="space-y-4 text-sm">
      <p>
        Duplicate: <strong>{picked.name}</strong>{" "}
        <span className="text-muted-foreground">({picked.email})</span>{" "}
        <button
          type="button"
          className="text-primary-ink hover:underline"
          onClick={() => {
            setPicked(null);
            setPlan(null);
            preview.reset();
          }}
        >
          Change
        </button>
      </p>

      {preview.isPending && <Spinner />}
      {preview.isError && <FormError message={msg(preview.error)} />}

      {plan && (
        <>
          <p>
            Moves to <strong>{survivorName}</strong>: {movesText(plan.moves)}
          </p>
          {plan.taken.length > 0 && (
            <p>
              <span className="text-muted-foreground">Taken from the duplicate:</span>{" "}
              {plan.taken.join(", ")}
            </p>
          )}
          {plan.conflicts.length > 0 && (
            <div>
              <p className="text-muted-foreground">Kept (the duplicate&apos;s value is dropped):</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                {plan.conflicts.map((c) => (
                  <li key={c.field}>
                    {c.field}: {show(c.kept)} (drops {show(c.dropped)})
                  </li>
                ))}
              </ul>
            </div>
          )}
          {plan.warnings.length > 0 && (
            <div
              role="note"
              className="flex gap-2 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning"
            >
              <TriangleAlert className="mt-0.5 size-3.5 flex-none" aria-hidden />
              <ul className="space-y-1">
                {plan.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </div>
          )}
          {!plan.can_merge && <FormError message={plan.blocked ?? "These can't be merged."} />}

          {plan.can_merge && (
            <label className="flex items-start gap-2">
              <input
                type="checkbox"
                className="mt-0.5 size-4 accent-primary"
                name="sure"
                checked={formik.values.sure}
                onChange={formik.handleChange}
              />
              I understand this can&apos;t be undone
            </label>
          )}
          {merge.isError && <FormError message={msg(merge.error)} />}
        </>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="danger"
          disabled={!plan?.can_merge || !formik.values.sure}
          loading={merge.isPending}
          onClick={() =>
            merge.mutate(picked.id, {
              onSuccess: (r) => {
                const n = r.moved?.tickets ?? 0;
                toast.success(`Merged. ${n} ${n === 1 ? "ticket" : "tickets"} moved.`);
                onClose();
              },
            })
          }
        >
          Merge
        </Button>
      </div>
    </div>
  );
}

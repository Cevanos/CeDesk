"use client";

import { Plus, Search, ShieldOff } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { CampaignTabs, CampaignsGate, errorText } from "@/components/campaigns/shared";
import { ConfirmDialog } from "@/components/ui/confirm";
import { InfiniteScroll } from "@/components/ui/infinite-scroll";
import { Dialog } from "@/components/ui/overlay";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Skeleton,
} from "@/components/ui/primitives";
import { DataTable, RowTh, TBody, Td, THead, Th, Tr } from "@/components/ui/table";
import { FormError, LoadError } from "@/components/workspace/list-shared";
import { useAddSuppression, useRemoveSuppression, useSuppressions } from "@/hooks/use-workspace";
import type { SuppressionEntry, SuppressionReason } from "@/lib/api/types";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";
import { cn, formatDateTime } from "@/lib/utils";

const REASONS: { value: SuppressionReason; label: string }[] = [
  { value: "unsubscribed", label: "Unsubscribed" },
  { value: "hard_bounce", label: "Hard bounce" },
  { value: "complaint", label: "Spam report" },
  { value: "manual", label: "Added by hand" },
  { value: "erased", label: "Erased customer" },
];
const REASON_LABEL = Object.fromEntries(REASONS.map((r) => [r.value, r.label])) as Record<
  string,
  string
>;
const SOURCE_LABEL: Record<string, string> = {
  link: "Unsubscribe page",
  one_click: "Mail app button",
  bounce: "Bounce",
  complaint: "Spam report",
  admin: "Admin",
  erase: "Customer erased",
};

/** Asking to be left alone is a decision a person made: removing it needs a reason. */
const needsConsent = (r: SuppressionReason) => r === "unsubscribed" || r === "complaint";

export function SuppressionsView() {
  return (
    <CampaignsGate>
      <CampaignTabs />
      <SuppressionList />
    </CampaignsGate>
  );
}

function SuppressionList() {
  const [reason, setReason] = useState("all");
  const [email, setEmail] = useState("");
  const find = useFormik({
    initialValues: { draft: "" },
    validationSchema: yup.object({
      draft: yup.string().trim().max(254, "That address is too long."),
    }),
    onSubmit: (v) => setEmail(v.draft.trim()),
  });
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<SuppressionEntry | null>(null);
  const list = useSuppressions({ reason, email });
  const rows = list.data?.pages.flatMap((p) => p.entries) ?? [];
  const counts = list.data?.pages[0]?.counts ?? {};

  return (
    <div>
      <p className="mb-4 text-sm text-muted-foreground">
        These addresses never get a campaign. Ticket replies and other normal emails still reach
        them.
      </p>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setReason("all")}
          aria-pressed={reason === "all"}
          className={chip(reason === "all")}
        >
          All
        </button>
        {REASONS.map((r) => (
          <button
            key={r.value}
            type="button"
            onClick={() => setReason(r.value)}
            aria-pressed={reason === r.value}
            className={chip(reason === r.value)}
          >
            {r.label}
            <span className="ml-1.5 tabular-nums opacity-70">{counts[r.value] ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <form
          role="search"
          noValidate
          className="flex items-center gap-2"
          onSubmit={find.handleSubmit}
        >
          <Input
            aria-label="Find one exact email address"
            type="search"
            placeholder="Find an exact address"
            className="w-full sm:w-64"
            {...bind(find, "draft")}
            onChange={(e) => {
              find.handleChange(e);
              if (!e.target.value) setEmail("");
            }}
          />
          <Button type="submit" variant="outline">
            <Search className="size-4" /> Find
          </Button>
        </form>
        <Button className="ml-auto" onClick={() => setAdding(true)}>
          <Plus className="size-4" /> Add address
        </Button>
      </div>

      {list.isPending ? (
        <Skeleton className="h-40" />
      ) : list.isError ? (
        <LoadError error={list.error} />
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState icon={<ShieldOff className="size-6" />} title="Nothing on the list">
            {email
              ? "That exact address is not on the suppression list."
              : "Addresses that unsubscribe or bounce show up here."}
          </EmptyState>
        </Card>
      ) : (
        <Card>
          <DataTable minWidth="56rem">
            <THead>
              <tr>
                <Th>Email</Th>
                <Th>Reason</Th>
                <Th>Source</Th>
                <Th>Campaign</Th>
                <Th>Note</Th>
                <Th>Added by</Th>
                <Th>Added at</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </THead>
            <TBody>
              {rows.map((e) => (
                <Tr key={e.id} className="align-top">
                  <RowTh className="font-normal">
                    {e.email ?? <span className="text-muted-foreground">(erased customer)</span>}
                  </RowTh>
                  <Td>
                    <Badge tone={e.reason === "complaint" ? "danger" : "neutral"}>
                      {REASON_LABEL[e.reason] ?? e.reason}
                    </Badge>
                  </Td>
                  <Td muted>{SOURCE_LABEL[e.source] ?? e.source}</Td>
                  <Td muted>{e.campaign_name ?? "—"}</Td>
                  <Td muted className="max-w-48">
                    {e.note ?? "—"}
                  </Td>
                  <Td muted>{e.created_by_name ?? "—"}</Td>
                  <Td muted className="whitespace-nowrap">
                    {formatDateTime(e.created_at)}
                  </Td>
                  <Td right>
                    {e.reason !== "erased" && (
                      <Button size="sm" variant="ghost" onClick={() => setRemoving(e)}>
                        Remove
                      </Button>
                    )}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </DataTable>
        </Card>
      )}

      <InfiniteScroll query={list} shown={rows.length} noun="entries" className="mt-2" />

      <AddDialog open={adding} onClose={() => setAdding(false)} />
      {removing && (
        <RemoveDialog key={removing.id} entry={removing} onClose={() => setRemoving(null)} />
      )}
    </div>
  );
}

const chip = (on: boolean) =>
  cn(
    "rounded-full border px-3 py-1 text-[13px] font-medium transition-colors",
    on
      ? "border-primary/40 bg-primary/10 text-primary-ink"
      : "border-border text-muted-foreground hover:bg-surface-2",
  );

function AddDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} onClose={onClose} title="Add address">
      {open && <AddForm onClose={onClose} />}
    </Dialog>
  );
}

function AddForm({ onClose }: { onClose: () => void }) {
  const add = useAddSuppression();
  const [error, setError] = useState<string>();
  const formik = useFormik({
    initialValues: { email: "", note: "" },
    validationSchema: yup.object({
      email: yup
        .string()
        .trim()
        .required("Enter an email address.")
        .email("Enter an email address."),
      note: yup.string().max(200, "Use at most 200 characters."),
    }),
    onSubmit: (v) => {
      setError(undefined);
      add.mutate(
        { email: v.email.trim(), note: v.note.trim() || undefined },
        {
          onSuccess: () => {
            toast.success("Address added");
            onClose();
          },
          onError: (err) => setError(errorText(err)),
        },
      );
    },
  });

  return (
    <form noValidate className="space-y-4" onSubmit={formik.handleSubmit}>
      <Field label="Email address" htmlFor="sp-email" error={fieldError(formik, "email")}>
        <Input id="sp-email" type="email" autoComplete="off" {...bind(formik, "email")} />
      </Field>
      <Field label="Note (optional)" htmlFor="sp-note" error={fieldError(formik, "note")}>
        <Input id="sp-note" maxLength={200} {...bind(formik, "note")} />
      </Field>
      <FormError message={error} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={add.isPending}>
          Add
        </Button>
      </div>
    </form>
  );
}

function RemoveDialog({ entry, onClose }: { entry: SuppressionEntry; onClose: () => void }) {
  const remove = useRemoveSuppression();
  const [error, setError] = useState<string>();
  const formik = useFormik({
    initialValues: { consent: false, how: "" },
    validationSchema: yup.object({
      consent: yup.boolean().oneOf([true], "Confirm that they asked to get emails again."),
      how: yup
        .string()
        .trim()
        .required("Say how they asked.")
        .min(10, "Use at least 10 characters."),
    }),
    onSubmit: () => run(),
  });
  const { consent, how } = formik.values;
  const strong = needsConsent(entry.reason);
  const address = entry.email ?? "this address";

  const run = () => {
    setError(undefined);
    const body = strong ? { confirm_consent: true, justification: how.trim() } : {};
    remove.mutate(
      { id: entry.id, ...body },
      {
        onSuccess: () => {
          toast.success("Removed from the suppression list");
          onClose();
        },
        onError: (e) => (strong ? setError(errorText(e)) : toast.error(errorText(e))),
      },
    );
  };

  if (!strong) {
    return (
      <ConfirmDialog
        open
        onClose={onClose}
        tone="warning"
        title="Remove from the suppression list?"
        message="This address can get campaigns again."
        detail={address}
        confirmLabel="Remove"
        busy={remove.isPending}
        onConfirm={run}
      />
    );
  }

  const how10 = how.trim().length >= 10;
  return (
    <Dialog open onClose={onClose} title="Remove this person?">
      <div className="space-y-4">
        <p className="text-sm">
          This person asked not to get these emails. Only remove them if they asked to get them
          again.
        </p>
        <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm font-medium">{address}</p>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            name="consent"
            checked={consent}
            onChange={formik.handleChange}
            className="mt-1"
          />
          They asked to get campaign emails again
        </label>
        <Field
          label="How did they ask?"
          htmlFor="sp-how"
          error={fieldError(formik, "how")}
          hint="At least 10 characters."
        >
          <Input id="sp-how" maxLength={200} {...bind(formik, "how")} />
        </Field>
        <FormError message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={!consent || !how10}
            loading={remove.isPending}
            onClick={() => void formik.submitForm()}
          >
            Remove
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

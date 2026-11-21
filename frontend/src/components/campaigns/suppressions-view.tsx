"use client";

import { Plus, Search, ShieldOff } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { CampaignTabs, CampaignsGate, errorText } from "@/components/campaigns/shared";
import { ConfirmDialog } from "@/components/ui/confirm";
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
import { FormError, LoadError } from "@/components/workspace/list-shared";
import { useAddSuppression, useRemoveSuppression, useSuppressions } from "@/hooks/use-workspace";
import type { SuppressionEntry, SuppressionReason } from "@/lib/api/types";
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
  const [draft, setDraft] = useState("");
  const [email, setEmail] = useState("");
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
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setEmail(draft.trim());
          }}
        >
          <Input
            aria-label="Find one exact email address"
            type="search"
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              if (!e.target.value) setEmail("");
            }}
            placeholder="Find an exact address"
            className="w-64"
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
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[56rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="px-4 py-2.5 font-medium">Email</th>
                <th className="px-3 py-2.5 font-medium">Reason</th>
                <th className="px-3 py-2.5 font-medium">Source</th>
                <th className="px-3 py-2.5 font-medium">Campaign</th>
                <th className="px-3 py-2.5 font-medium">Note</th>
                <th className="px-3 py-2.5 font-medium">Added by</th>
                <th className="px-3 py-2.5 font-medium">Added at</th>
                <th className="px-3 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((e) => (
                <tr key={e.id} className="align-top">
                  <td className="px-4 py-2.5">
                    {e.email ?? <span className="text-muted-foreground">(erased customer)</span>}
                  </td>
                  <td className="px-3 py-2.5">
                    <Badge tone={e.reason === "complaint" ? "danger" : "neutral"}>
                      {REASON_LABEL[e.reason] ?? e.reason}
                    </Badge>
                  </td>
                  <td className="px-3 py-2.5 text-muted-foreground">
                    {SOURCE_LABEL[e.source] ?? e.source}
                  </td>
                  <td className="px-3 py-2.5 text-muted-foreground">{e.campaign_name ?? "—"}</td>
                  <td className="max-w-48 px-3 py-2.5 text-muted-foreground">{e.note ?? "—"}</td>
                  <td className="px-3 py-2.5 text-muted-foreground">{e.created_by_name ?? "—"}</td>
                  <td className="px-3 py-2.5 whitespace-nowrap text-muted-foreground">
                    {formatDateTime(e.created_at)}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    {e.reason !== "erased" && (
                      <Button size="sm" variant="ghost" onClick={() => setRemoving(e)}>
                        Remove
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {list.hasNextPage && (
        <div className="mt-4 flex justify-center">
          <Button
            variant="outline"
            loading={list.isFetchingNextPage}
            onClick={() => list.fetchNextPage()}
          >
            Load more
          </Button>
        </div>
      )}

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
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string>();

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (!email.includes("@")) return setError("Enter an email address.");
        setError(undefined);
        add.mutate(
          { email: email.trim(), note: note.trim() || undefined },
          {
            onSuccess: () => {
              toast.success("Address added");
              onClose();
            },
            onError: (err) => setError(errorText(err)),
          },
        );
      }}
    >
      <Field label="Email address" htmlFor="sp-email">
        <Input
          id="sp-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="off"
        />
      </Field>
      <Field label="Note (optional)" htmlFor="sp-note">
        <Input
          id="sp-note"
          value={note}
          maxLength={200}
          onChange={(e) => setNote(e.target.value)}
        />
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
  const [consent, setConsent] = useState(false);
  const [how, setHow] = useState("");
  const [error, setError] = useState<string>();
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
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
            className="mt-1"
          />
          They asked to get campaign emails again
        </label>
        <Field label="How did they ask?" htmlFor="sp-how" hint="At least 10 characters.">
          <Input id="sp-how" value={how} maxLength={200} onChange={(e) => setHow(e.target.value)} />
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
            onClick={run}
          >
            Remove
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

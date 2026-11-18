"use client";

import { ChevronRight, LogOut, Plus, Send, Ticket } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { publicErrorMessage, publicFieldError } from "@/components/public/errors";
import { PortalSignIn } from "@/components/public/portal-signin";
import {
  type FieldValues,
  TypeFields,
  fieldsPayload,
  missingFields,
} from "@/components/public/type-fields";
import { StatusBadge } from "@/components/tickets/badges";
import {
  Button,
  Card,
  EmptyState,
  Field,
  Select,
  Skeleton,
  Textarea,
} from "@/components/ui/primitives";
import { usePortalLiveUpdates } from "@/hooks/use-live-stream";
import {
  isUnauthorized,
  useCreatePortalTicket,
  usePortalSignOut,
  usePortalTicketTypes,
  usePortalTickets,
  useWorkspaceSlug,
} from "@/hooks/use-portal";
import { timeAgo } from "@/lib/utils";

/** `/<workspace>/portal`: sign in with an emailed code, then see and open tickets. */
export function PortalHome() {
  const ws = useWorkspaceSlug();
  const tickets = usePortalTickets(ws);
  usePortalLiveUpdates(tickets.isSuccess);
  const signOut = usePortalSignOut();
  const [creating, setCreating] = useState(false);

  if (tickets.isPending) {
    return (
      <div className="mx-auto max-w-3xl space-y-3">
        <Skeleton className="h-10 w-56" />
        <Skeleton className="h-20 rounded-xl" />
        <Skeleton className="h-20 rounded-xl" />
      </div>
    );
  }
  if (isUnauthorized(tickets.error)) return <PortalSignIn />;
  if (tickets.isError) {
    return (
      <p role="alert" className="mx-auto max-w-xl rounded-xl bg-danger-soft p-4 text-sm text-danger">
        {publicErrorMessage(tickets.error)}
      </p>
    );
  }

  return (
    <div className="mx-auto max-w-3xl animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">My tickets</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Follow your requests and reply to our team.
          </p>
        </div>
        <div className="flex gap-2">
          {!creating && (
            <Button onClick={() => setCreating(true)}>
              <Plus className="size-4" /> New ticket
            </Button>
          )}
          <Button
            variant="outline"
            loading={signOut.isPending}
            onClick={() => signOut.mutate()}
          >
            <LogOut className="size-4" /> Sign out
          </Button>
        </div>
      </div>

      {creating && <NewTicketForm onDone={() => setCreating(false)} />}

      {tickets.data.length === 0 ? (
        <Card className="mt-6 rounded-2xl">
          <EmptyState icon={<Ticket className="size-6" />} title="No tickets yet">
            When you contact us, your requests will show up here.
          </EmptyState>
        </Card>
      ) : (
        <ul className="mt-6 space-y-3">
          {tickets.data.map((t) => (
            <li key={t.id}>
              <Link
                href={`/${encodeURIComponent(ws)}/tickets/${encodeURIComponent(t.id)}`}
                className="group flex items-center gap-4 rounded-2xl border border-border bg-surface p-4 shadow-card transition-all hover:-translate-y-0.5 hover:border-border-strong hover:shadow-pop focus-visible:ring-4 focus-visible:ring-primary/25 focus-visible:outline-none"
              >
                <span className="grid size-11 flex-none place-items-center rounded-xl bg-primary-soft text-primary-ink">
                  <Ticket className="size-5" aria-hidden />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{t.subject}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    {t.label} · updated {timeAgo(t.updated_at)}
                  </span>
                </span>
                <StatusBadge status={t.status} label={t.status_label} />
                <ChevronRight
                  className="size-4 flex-none text-subtle-foreground transition-transform group-hover:translate-x-0.5"
                  aria-hidden
                />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function NewTicketForm({ onDone }: { onDone: () => void }) {
  const ws = useWorkspaceSlug();
  const types = usePortalTicketTypes(ws);
  const create = useCreatePortalTicket(ws);
  const [description, setDescription] = useState("");
  const [typeId, setTypeId] = useState("");
  const [values, setValues] = useState<FieldValues>({});
  const typeFields = (types.data ?? []).find((t) => t.id === typeId)?.fields ?? [];
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [sent, setSent] = useState(false);

  if (sent) {
    return (
      <Card className="mt-6 rounded-2xl border-success/30 bg-success-soft p-5">
        <p role="status" className="text-sm font-medium text-success">
          Thanks, we got your request. We&apos;ll email you when we reply; you&apos;ll see the
          conversation here.
        </p>
        <Button className="mt-3" size="sm" variant="outline" onClick={onDone}>
          Close
        </Button>
      </Card>
    );
  }

  return (
    <Card className="mt-6 rounded-2xl p-5 shadow-pop sm:p-6">
      <h2 className="text-lg font-semibold tracking-tight">New ticket</h2>
      <form
        noValidate
        className="mt-4 space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          const next: Record<string, string> = {};
          if (!description.trim()) next.description = "Describe what happened.";
          Object.assign(next, missingFields(typeFields, values));
          setErrs(next);
          if (Object.keys(next).length) return;
          create.mutate(
            {
              // The subject is made by the server from the start of the message.
              message: description.trim(),
              ...(typeId ? { type_id: typeId, fields: fieldsPayload(typeFields, values) } : {}),
            },
            {
              onSuccess: () => setSent(true),
              onError: (err) =>
                setErrs({
                  description: publicFieldError(err, "message") ?? "",
                  _form: publicErrorMessage(err),
                }),
            },
          );
        }}
      >
        {(types.data?.length ?? 0) > 0 && (
          <Field label="What is it about?" htmlFor="pt-type">
            <Select
              id="pt-type"
              value={typeId}
              placeholder="Choose one (optional)"
              options={(types.data ?? []).map((t) => ({ value: t.id, label: t.name }))}
              onChange={(e) => {
                setTypeId(e.target.value);
                setValues({});
              }}
            />
          </Field>
        )}
        <TypeFields
          idPrefix="pt"
          fields={typeFields}
          values={values}
          errors={errs}
          onChange={(k, v) => setValues((x) => ({ ...x, [k]: v }))}
        />
        <Field label="How can we help?" htmlFor="pt-desc" error={errs.description}>
          <Textarea
            id="pt-desc"
            rows={5}
            maxLength={10000}
            value={description}
            aria-invalid={!!errs.description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        {errs._form && !errs.description && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            {errs._form}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onDone}>
            Cancel
          </Button>
          <Button type="submit" loading={create.isPending}>
            <Send className="size-4" /> Send request
          </Button>
        </div>
      </form>
    </Card>
  );
}

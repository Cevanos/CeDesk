"use client";

import { ChevronRight, LogOut, Plus, Send, Ticket } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { publicErrorMessage, publicFieldError } from "@/components/public/errors";
import { PortalSignIn } from "@/components/public/portal-signin";
import {
  TypeFields,
  fieldsPayload,
  typeFieldErrors,
  typeFieldsSchema,
} from "@/components/public/type-fields";
import { StatusBadge } from "@/components/tickets/badges";
import { AiBadge } from "@/components/ui/ai";
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
import { bind, bindSelect, fieldError, useFormik, yup, yupValidate } from "@/lib/forms";
import { cn, timeAgo } from "@/lib/utils";

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
  if (isUnauthorized(tickets.error)) {
    // Had a list before = was signed in and got signed out: say why (e.g. domain list changed).
    return <PortalSignIn notice={tickets.data ? publicErrorMessage(tickets.error) : undefined} />;
  }
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
                <span className="bg-ai-gradient grid size-11 flex-none place-items-center rounded-xl text-white shadow-[0_6px_16px_-8px_var(--ai-from)]">
                  <Ticket className="size-5" aria-hidden />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{t.subject}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    {t.label} · updated {timeAgo(t.updated_at)}
                    {t.owner_first_name ? ` · Handled by ${t.owner_first_name}` : ""}
                  </span>
                  {t.pending_step && (
                    <span
                      className={cn(
                        "mt-1.5 inline-block rounded-md px-2 py-0.5 text-xs font-medium",
                        t.status === "pending"
                          ? "bg-warning-soft text-warning"
                          : "bg-primary-soft text-primary-ink",
                      )}
                    >
                      Next step: {t.pending_step}
                    </span>
                  )}
                  {t.status === "resolved" && t.outcome && (
                    <span className="mt-1.5 block text-xs text-foreground-2">
                      Outcome: {t.outcome}
                    </span>
                  )}
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
  const [sent, setSent] = useState(false);
  const [formError, setFormError] = useState("");
  const fieldsOf = (typeId: string) => (types.data ?? []).find((t) => t.id === typeId)?.fields ?? [];

  const formik = useFormik<{ description: string; type_id: string; fields: Record<string, string> }>({
    initialValues: { description: "", type_id: "", fields: {} },
    validate: yupValidate((v) =>
      yup.object({
        description: yup
          .string()
          .trim()
          .required("Describe what happened.")
          .max(10000, "Use at most 10,000 characters."),
        type_id: yup.string(),
        fields: typeFieldsSchema(fieldsOf(v.type_id)),
      }),
    ),
    onSubmit: (v, helpers) => {
      setFormError("");
      create.mutate(
        {
          // The subject is made by the server from the start of the message.
          message: v.description.trim(),
          ...(v.type_id
            ? { type_id: v.type_id, fields: fieldsPayload(fieldsOf(v.type_id), v.fields) }
            : {}),
        },
        {
          onSuccess: () => setSent(true),
          onError: (err) => {
            const m = publicFieldError(err, "message");
            if (m) helpers.setFieldError("description", m);
            else setFormError(publicErrorMessage(err));
          },
        },
      );
    },
  });

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
    <Card className="ai-frame mt-6 rounded-2xl p-5 shadow-pop sm:p-6">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold tracking-tight">New ticket</h2>
        <AiBadge>Auto-triaged</AiBadge>
      </div>
      <form noValidate className="mt-4 space-y-4" onSubmit={formik.handleSubmit}>
        {(types.data?.length ?? 0) > 0 && (
          <Field label="What is it about?" htmlFor="pt-type">
            <Select
              id="pt-type"
              {...bindSelect(formik, "type_id")}
              placeholder="Choose one (optional)"
              options={(types.data ?? []).map((t) => ({ value: t.id, label: t.name }))}
              onChange={(e) =>
                void formik.setValues({
                  ...formik.values,
                  type_id: e.target.value,
                  fields: Object.fromEntries(fieldsOf(e.target.value).map((f) => [f.key, ""])),
                })
              }
            />
          </Field>
        )}
        <TypeFields
          idPrefix="pt"
          fields={fieldsOf(formik.values.type_id)}
          values={formik.values.fields}
          errors={typeFieldErrors(formik)}
          onChange={(k, v) => void formik.setFieldValue(`fields.${k}`, v)}
        />
        <Field label="How can we help?" htmlFor="pt-desc" error={fieldError(formik, "description")}>
          <Textarea id="pt-desc" rows={5} maxLength={10000} {...bind(formik, "description")} />
        </Field>
        {formError && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            {formError}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onDone}>
            Cancel
          </Button>
          <Button type="submit" variant="ai" loading={create.isPending}>
            <Send className="size-4" /> Send request
          </Button>
        </div>
      </form>
    </Card>
  );
}

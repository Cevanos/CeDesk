"use client";

import {
  ArrowLeft,
  FileText,
  ListChecks,
  Route,
  SlidersHorizontal,
  Sparkles,
  Ticket,
  UserRound,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId } from "react";
import { toast } from "sonner";

import { PRIORITIES, PRIORITY_LABEL, PriorityBadge } from "@/components/tickets/badges";
import { FromChatCard } from "@/components/tickets/from-chat-card";
import { PageHeader } from "@/components/ui/page-header";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  FormSelect,
  Input,
  Textarea,
} from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { useCreateTicket } from "@/hooks/use-tickets";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import { useAgents, useTeams, useTicketTypes } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { Priority } from "@/lib/api/types";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";
import { cn } from "@/lib/utils";

/** Channels the API accepts on POST /tickets. */
const CHANNELS = ["email", "web", "portal", "api", "chat", "phone"];

const schema = yup.object({
  subject: yup.string().trim().required("Enter a subject").max(200),
  name: yup.string().trim().required("Enter the customer's name").max(120),
  email: yup.string().trim().required("Enter the customer's email").email("Enter a valid email"),
  description: yup.string().max(20000),
  priority: yup.string().oneOf(["urgent", "high", "medium", "low"]).required(),
  channel: yup.string().required(),
  type_id: yup.string(),
  team_id: yup.string(),
  assignee_id: yup.string(),
  tags: yup.string(),
  fields: yup.object(),
});
type Values = {
  subject: string;
  name: string;
  email: string;
  description: string;
  priority: string;
  channel: string;
  type_id: string;
  team_id: string;
  assignee_id: string;
  tags: string;
  fields: Record<string, string>;
};
const INITIAL: Values = {
  subject: "",
  name: "",
  email: "",
  description: "",
  priority: "medium",
  channel: "web",
  type_id: "",
  team_id: "",
  assignee_id: "",
  tags: "",
  fields: {},
};

export function NewTicketForm() {
  const router = useRouter();
  const { user, perms, isPending } = useCurrentUser();
  const create = useCreateTicket();
  const types = useTicketTypes();
  const teams = useTeams();
  const agents = useAgents();

  const form = useFormik<Values>({
    initialValues: INITIAL,
    validationSchema: schema,
    onSubmit: (v, { setFieldError, setFieldTouched }) => submit(v, setFieldError, setFieldTouched),
  });
  const { values } = form;

  // Warn before a reload or tab close throws away a half-filled ticket.
  useUnsavedChanges(form.dirty && !create.isSuccess);

  const typeId = values.type_id;
  const teamId = values.team_id;
  const priority = values.priority;
  const channel = values.channel;
  const subject = values.subject;
  const customerName = values.name;
  const assigneeId = values.assignee_id;
  const type = types.data?.find((t) => t.id === typeId);
  const assignable = (agents.data ?? []).filter(
    (a) => a.assignable !== false && (perms.assignOthers || a.member_id === user?.id),
  );

  if (!isPending && !perms.createTicket) {
    return (
      <EmptyState title="You can't create tickets">
        Light agents can read tickets and add private notes.
      </EmptyState>
    );
  }

  function submit(
    v: Values,
    setFieldError: (f: string, m: string | undefined) => void,
    setFieldTouched: (f: string, t?: boolean, validate?: boolean) => unknown,
  ) {
    // Only fields that belong to the chosen type are sent.
    const fields: Record<string, string | number> = {};
    for (const f of type?.fields ?? []) {
      const raw = v.fields?.[f.key];
      if (raw) fields[f.key] = f.kind === "number" ? Number(raw) : raw;
    }
    create.mutate(
      {
        subject: v.subject.trim(),
        contact: { name: v.name.trim(), email: v.email.trim() },
        description: v.description || undefined,
        priority: v.priority as Priority,
        channel: v.channel,
        type_id: v.type_id || null,
        team_id: v.team_id || null,
        assignee_id: v.assignee_id || null,
        fields,
        tags: (v.tags ?? "")
          .split(",")
          .map((t) => t.trim().toLowerCase())
          .filter(Boolean)
          .slice(0, 20),
      },
      {
        onSuccess: (t) => {
          toast.success(`Ticket ${t.label ?? ""} created`);
          router.push(`/tickets/${t.id}`);
        },
        onError: (e) => {
          if (e instanceof ApiError && e.status === 422) {
            for (const d of e.details) {
              const key = d.field.startsWith("contact.")
                ? d.field.split(".")[1] === "email"
                  ? "email"
                  : "name"
                : d.field;
              if (key in schema.fields) {
                setFieldTouched(key, true, false);
                setFieldError(key, d.message);
              } else toast.error(d.message);
            }
          }
        },
      },
    );
  }

  const teamName = teams.data?.find((t) => t.id === teamId)?.name;
  const assigneeName = assignable.find((a) => a.member_id === assigneeId)?.name;

  const routing = assigneeName
    ? `Assigned to ${assigneeName}`
    : teamId
      ? `Auto-assigned within ${teamName ?? "the team"}`
      : "Unassigned until someone picks it up";

  return (
    <div className="mx-auto w-full max-w-6xl px-3 pt-4 pb-28 sm:px-6 sm:pt-6 lg:pb-10">
      <nav aria-label="Breadcrumb" className="mb-4 flex items-center gap-1.5 text-[13px]">
        <Link
          href="/tickets"
          className="inline-flex items-center gap-1 rounded-md py-0.5 pr-1.5 text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" /> Tickets
        </Link>
        <span aria-hidden className="text-subtle-foreground">
          /
        </span>
        <span className="font-medium text-foreground-2">New ticket</span>
      </nav>

      <PageHeader
        icon={Ticket}
        title="Create a ticket"
        description="Log a request on behalf of a customer. Choose a team and it is routed for you."
      />

      <form
        onSubmit={form.handleSubmit}
        noValidate
        className="mt-5 grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_21rem]"
      >
        <div className="min-w-0 space-y-5">
          <Panel icon={<FileText className="size-4" />} title="The request" step={1}>
            <Field label="Subject" htmlFor="subject" error={fieldError(form, "subject")}>
              <Input
                id="subject"
                placeholder="e.g. Order not delivered"
                {...bind(form, "subject")}
              />
            </Field>
            <Field
              label="First message"
              htmlFor="description"
              hint="Optional. Shown at the top of the conversation."
            >
              <Textarea
                id="description"
                rows={6}
                placeholder="What does the customer need help with?"
                {...bind(form, "description")}
              />
            </Field>
          </Panel>

          <Panel icon={<UserRound className="size-4" />} title="The customer" step={2}>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Name" htmlFor="name" error={fieldError(form, "name")}>
                <Input
                  id="name"
                  autoComplete="off"
                  placeholder="Asha Verma"
                  {...bind(form, "name")}
                />
              </Field>
              <Field label="Email" htmlFor="email" error={fieldError(form, "email")}>
                <Input
                  id="email"
                  type="email"
                  autoComplete="off"
                  placeholder="asha@example.com"
                  {...bind(form, "email")}
                />
              </Field>
            </div>
          </Panel>

          {type && type.fields.length > 0 && (
            <Panel icon={<ListChecks className="size-4" />} title={`${type.name} details`} step={3}>
              <div className="grid gap-4 sm:grid-cols-2">
                {type.fields.map((f) => (
                  <Field
                    key={f.key}
                    label={`${f.label}${f.required ? " *" : ""}`}
                    htmlFor={`fld-${f.key}`}
                  >
                    {f.kind === "select" ? (
                      <FormSelect form={form} id={`fld-${f.key}`} name={`fields.${f.key}`}>
                        <option value="">—</option>
                        {f.options?.map((o) => (
                          <option key={o} value={o}>
                            {o}
                          </option>
                        ))}
                      </FormSelect>
                    ) : (
                      <Input
                        id={`fld-${f.key}`}
                        type={f.kind === "number" ? "number" : "text"}
                        {...bind(form, `fields.${f.key}`)}
                      />
                    )}
                  </Field>
                ))}
              </div>
            </Panel>
          )}

          <FromChatCard />
        </div>

        <aside aria-label="Ticket settings" className="min-w-0 space-y-5 lg:sticky lg:top-20">
          <Panel icon={<SlidersHorizontal className="size-4" />} title="Properties">
            <Segmented
              legend="Priority"
              value={priority}
              onChange={(v) => void form.setFieldValue("priority", v)}
              options={PRIORITIES.map((p) => ({
                value: p,
                label: PRIORITY_LABEL[p],
                dot: PRIORITY_DOT[p],
              }))}
            />
            <Segmented
              legend="Channel"
              value={channel}
              onChange={(v) => void form.setFieldValue("channel", v)}
              options={CHANNELS.map((c) => ({
                value: c,
                label: c.charAt(0).toUpperCase() + c.slice(1),
              }))}
            />
            <Field label="Type" htmlFor="type_id">
              <FormSelect form={form} id="type_id" name="type_id">
                <option value="">None</option>
                {types.data?.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </FormSelect>
            </Field>
            <Field label="Tags" htmlFor="tags" hint="Comma separated, up to 20.">
              <Input id="tags" placeholder="delivery, vip" {...bind(form, "tags")} />
            </Field>
          </Panel>

          <Panel icon={<Route className="size-4" />} title="Routing">
            <Field label="Team" htmlFor="team_id">
              <FormSelect form={form} id="team_id" name="team_id">
                <option value="">No team</option>
                {teams.data?.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </FormSelect>
            </Field>
            <Field label="Assignee" htmlFor="assignee_id">
              <FormSelect form={form} id="assignee_id" name="assignee_id">
                <option value="">{teamId ? "Auto-assign" : "Unassigned"}</option>
                {assignable.map((a) => (
                  <option key={a.member_id} value={a.member_id}>
                    {a.name}
                  </option>
                ))}
              </FormSelect>
            </Field>
            <p className="flex items-start gap-2 rounded-lg bg-surface-2 px-3 py-2.5 text-xs text-muted-foreground">
              <Sparkles className="mt-0.5 size-3.5 flex-none text-primary" aria-hidden />
              {routing}
            </p>
          </Panel>

          <Card className="hidden space-y-3 rounded-2xl p-4 lg:block">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone="neutral">{(channel ?? "web").toUpperCase()}</Badge>
              {priority && <PriorityBadge priority={priority as Priority} />}
            </div>
            <p className="line-clamp-2 text-sm font-medium text-foreground-2">
              {subject?.trim() || "Your subject will appear here"}
            </p>
            {customerName?.trim() && (
              <p className="text-xs text-muted-foreground">for {customerName.trim()}</p>
            )}
            <div className="flex gap-2 pt-1">
              <Link href="/tickets" className="flex-none">
                <Button variant="ghost">Cancel</Button>
              </Link>
              <Button type="submit" size="lg" className="flex-1" loading={create.isPending}>
                Create ticket
              </Button>
            </div>
          </Card>
        </aside>

        {/* Mobile and tablet: actions stay reachable while scrolling. */}
        <div className="fixed inset-x-0 bottom-0 z-20 flex items-center gap-2 border-t border-border bg-surface/95 px-4 py-3 backdrop-blur lg:hidden">
          <Link href="/tickets" className="flex-none">
            <Button variant="ghost">Cancel</Button>
          </Link>
          <Button type="submit" size="lg" className="flex-1" loading={create.isPending}>
            Create ticket
          </Button>
        </div>
      </form>
    </div>
  );
}

const PRIORITY_DOT: Record<Priority, string> = {
  urgent: "bg-danger",
  high: "bg-warning",
  medium: "bg-info",
  low: "bg-border-strong",
};

function Panel({
  icon,
  title,
  step,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  step?: number;
  children: React.ReactNode;
}) {
  return (
    <Card className="rounded-2xl p-4 sm:p-5">
      <h2 className="mb-4 flex items-center gap-2.5 text-[13px] font-semibold text-foreground">
        <span className="grid size-7 flex-none place-items-center rounded-lg bg-primary-soft text-primary-ink">
          {icon}
        </span>
        {title}
        {step && (
          <span className="ml-auto text-[11px] font-medium text-subtle-foreground">
            Step {step}
          </span>
        )}
      </h2>
      <div className="space-y-4">{children}</div>
    </Card>
  );
}

function Segmented({
  legend,
  value,
  onChange,
  options,
}: {
  legend: string;
  value?: string;
  onChange: (v: string) => void;
  options: { value: string; label: string; dot?: string }[];
}) {
  const name = useId();
  return (
    <fieldset>
      <legend className="mb-1.5 text-[13px] font-medium text-foreground-2">{legend}</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => {
          const on = value === o.value;
          return (
            <label
              key={o.value}
              className={cn(
                "inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] font-medium transition-colors has-focus-visible:ring-4 has-focus-visible:ring-primary/15",
                on
                  ? "border-primary/40 bg-primary-soft text-primary-ink shadow-card"
                  : "border-border bg-surface text-muted-foreground hover:border-border-strong hover:text-foreground",
              )}
            >
              <input
                type="radio"
                name={name}
                value={o.value}
                checked={on}
                onChange={() => onChange(o.value)}
                className="sr-only"
              />
              {o.dot && <span aria-hidden className={cn("size-2 rounded-full", o.dot)} />}
              {o.label}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

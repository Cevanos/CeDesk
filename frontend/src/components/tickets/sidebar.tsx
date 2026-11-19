"use client";

import { Layers, Paperclip, Plus, SlidersHorizontal, Upload, UserRound, X } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { ApprovalsCard } from "@/components/tickets/approvals-card";
import {
  PRIORITIES,
  PRIORITY_LABEL,
  STATUSES,
  STATUS_LABEL,
  VipBadge,
} from "@/components/tickets/badges";
import { OrdersCard, PaymentsCard } from "@/components/tickets/commerce-cards";
import { CsatAverage, CsatCard } from "@/components/tickets/csat";
import { ImportedCard, importedRows, isImported } from "@/components/tickets/imported-card";
import { JiraCard } from "@/components/tickets/jira-card";
import { FileChip } from "@/components/tickets/message-files";
import { RelatedCard } from "@/components/tickets/related-card";
import { SideConversations } from "@/components/tickets/side-conversations";
import { SlaPanel } from "@/components/tickets/sla";
import { TasksCard } from "@/components/tickets/tasks-card";
import { TimeCard } from "@/components/tickets/time-card";
import { useConfirm } from "@/components/ui/confirm";
import { Badge, Card, Input, Select, Skeleton } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import {
  useAttachments,
  useUpdateTicket,
  useUploadAttachment,
  type TicketPatch,
} from "@/hooks/use-tickets";
import { useAgents, useTeams, useTicketTypes } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { Priority, Ticket, TicketStatus } from "@/lib/api/types";
import { cn, formatDateTime, initials } from "@/lib/utils";

/** Compact, borderless-until-hover controls for the properties panel. */
const sel =
  "min-h-9 border-transparent bg-surface-2/70 text-[13px] font-medium hover:border-border-strong hover:bg-surface focus:bg-surface";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="rounded-2xl p-4 sm:p-5">
      <h3 className="mb-4 flex items-center gap-2 text-[11px] font-bold tracking-[0.08em] text-muted-foreground uppercase after:h-px after:flex-1 after:bg-border">
        {title}
      </h3>
      {children}
    </Card>
  );
}

function Row({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] items-center gap-3 border-b border-border/70 py-2 first:pt-0 last:border-b-0 last:pb-0">
      <label htmlFor={htmlFor} className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}

export function TicketSidebar({ ticket, etag }: { ticket: Ticket; etag: string | null }) {
  const { user, perms } = useCurrentUser();
  const update = useUpdateTicket(ticket.id);
  const teams = useTeams();
  const types = useTicketTypes();
  const agents = useAgents();
  const { ask, dialog } = useConfirm();
  const [tag, setTag] = useState("");
  const [tab, setTab] = useState<"details" | "customer" | "files" | "more">("details");

  const patch = (p: TicketPatch) =>
    update.mutate(
      { patch: p, etag },
      {
        onError: (e) => {
          // 422: show each field message.
          if (e instanceof ApiError && e.status === 422) {
            toast.error(e.details.map((d) => d.message).join(" ") || e.userMessage);
          }
        },
      },
    );

  // `can_edit` comes from the API (view-only tickets); the role matrix still applies.
  const canEdit = perms.editTicket && (ticket as { can_edit?: boolean }).can_edit !== false;
  const disabled = !canEdit || update.isPending;
  const type = types.data?.find((t) => t.id === ticket.type_id);
  const showImported =
    isImported(ticket) && (importedRows(ticket, type).length > 0 || !!ticket.fields?.source_url);

  // Supervisors/admins can assign to anyone; agents only to themselves.
  const assignable = (agents.data ?? []).filter(
    (a) => perms.assignOthers || a.member_id === user?.id,
  );

  const TABS = [
    { id: "details", label: "Details", icon: SlidersHorizontal },
    { id: "customer", label: "Customer", icon: UserRound },
    { id: "files", label: "Files", icon: Paperclip },
    { id: "more", label: "More", icon: Layers },
  ] as const;

  return (
    <div className="space-y-4">
      {dialog}
      <div
        role="tablist"
        aria-label="Ticket sections"
        className="grid grid-cols-4 gap-1 rounded-xl border border-border bg-surface-2 p-1"
      >
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            role="tab"
            id={`tab-${id}`}
            aria-selected={tab === id}
            aria-controls={`panel-${id}`}
            onClick={() => setTab(id)}
            className={cn(
              "inline-flex items-center justify-center gap-1 rounded-lg px-1.5 py-1.5 text-[12px] font-semibold transition-all sm:text-[13px]",
              tab === id
                ? "bg-surface text-foreground shadow-card ring-1 ring-border"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon className="size-3.5" /> {label}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id="panel-details"
        aria-labelledby="tab-details"
        hidden={tab !== "details"}
        className="space-y-4"
      >
        <Section title="Properties">
          <Row label="Status" htmlFor="p-status">
            <Select
              id="p-status"
              controlClassName={sel}
              disabled={disabled}
              value={ticket.status}
              onChange={(e) => patch({ status: e.target.value as TicketStatus })}
            >
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </Select>
          </Row>
          <Row label="Priority" htmlFor="p-priority">
            <Select
              id="p-priority"
              controlClassName={sel}
              disabled={disabled}
              value={ticket.priority}
              onChange={(e) => patch({ priority: e.target.value as Priority })}
            >
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {PRIORITY_LABEL[p]}
                </option>
              ))}
            </Select>
          </Row>
          <Row label="Type" htmlFor="p-type">
            <Select
              id="p-type"
              controlClassName={sel}
              disabled={disabled}
              value={ticket.type_id ?? ""}
              onChange={(e) => {
                const next = e.target.value || null;
                if (isImported(ticket) && importedRows(ticket, type).length > 0) {
                  ask({
                    title: "Change the ticket type?",
                    message:
                      "Fields from the old help desk that are not in this type will be removed.",
                    confirmLabel: "Change type",
                    tone: "warning",
                    run: () => patch({ type_id: next }),
                  });
                  return;
                }
                patch({ type_id: next });
              }}
            >
              <option value="">None</option>
              {types.data?.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </Select>
          </Row>
          <Row label="Team" htmlFor="p-team">
            <Select
              id="p-team"
              controlClassName={sel}
              disabled={disabled}
              value={ticket.team_id ?? ""}
              onChange={(e) => patch({ team_id: e.target.value || null })}
            >
              <option value="">No team</option>
              {teams.data?.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </Select>
          </Row>
          <Row label="Assignee" htmlFor="p-assignee">
            <Select
              id="p-assignee"
              controlClassName={sel}
              disabled={disabled || !(perms.assignSelf || perms.assignOthers)}
              value={ticket.assignee_id ?? ""}
              onChange={(e) => patch({ assignee_id: e.target.value || null })}
            >
              <option value="">Unassigned</option>
              {/* keep the current assignee visible even if the viewer cannot pick them */}
              {ticket.assignee_id &&
                !assignable.some((a) => a.member_id === ticket.assignee_id) && (
                  <option value={ticket.assignee_id}>{ticket.assignee_name}</option>
                )}
              {assignable.map((a) => (
                <option key={a.member_id} value={a.member_id}>
                  {a.name}
                </option>
              ))}
            </Select>
          </Row>
          {!perms.editTicket && (
            <p className="text-xs text-muted-foreground">
              Light agents can read and add private notes only.
            </p>
          )}
        </Section>

        {type && type.fields.length > 0 && (
          <Section title={`${type.name} details`}>
            {type.fields.map((f) => {
              const current = ticket.fields?.[f.key];
              const id = `f-${f.key}`;
              const commit = (raw: string) => {
                if (String(current ?? "") === raw) return;
                patch({
                  fields: { [f.key]: f.kind === "number" && raw !== "" ? Number(raw) : raw },
                });
              };
              return (
                <Row key={f.key} label={`${f.label}${f.required ? " *" : ""}`} htmlFor={id}>
                  {f.kind === "select" ? (
                    <Select
                      id={id}
                      controlClassName={sel}
                      disabled={disabled}
                      value={String(current ?? "")}
                      onChange={(e) => commit(e.target.value)}
                    >
                      <option value="">—</option>
                      {f.options?.map((o) => (
                        <option key={o} value={o}>
                          {o}
                        </option>
                      ))}
                    </Select>
                  ) : (
                    <Input
                      id={id}
                      key={`${id}-${etag}`}
                      className={sel}
                      type={f.kind === "number" ? "number" : "text"}
                      disabled={disabled}
                      defaultValue={String(current ?? "")}
                      onBlur={(e) => commit(e.target.value)}
                    />
                  )}
                </Row>
              );
            })}
          </Section>
        )}

        {showImported && (
          <Card className="rounded-2xl p-4 sm:p-5">
            <ImportedCard ticket={ticket} type={type} />
          </Card>
        )}

        <Section title="Tags">
          <div className="flex flex-wrap gap-1.5">
            {(ticket.tags ?? []).map((t) => (
              <Badge key={t} tone="primary" className="gap-1 pr-1">
                {t}
                {perms.editTicket && (
                  <button
                    aria-label={`Remove tag ${t}`}
                    onClick={() => patch({ remove_tags: [t] })}
                  >
                    <X className="size-3" />
                  </button>
                )}
              </Badge>
            ))}
            {(ticket.tags ?? []).length === 0 && (
              <span className="text-sm text-muted-foreground">No tags</span>
            )}
          </div>
          {perms.editTicket && (
            <form
              className="mt-3 flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                const v = tag.trim().toLowerCase();
                if (!v || (ticket.tags ?? []).includes(v)) return;
                if ((ticket.tags ?? []).length >= 20)
                  return toast.error("A ticket can have at most 20 tags.");
                patch({ add_tags: [v] });
                setTag("");
              }}
            >
              <Input
                value={tag}
                onChange={(e) => setTag(e.target.value)}
                placeholder="Add tag"
                aria-label="Add tag"
                maxLength={40}
                className="h-9"
              />
              <button
                className="grid size-9 flex-none place-items-center rounded-lg bg-surface-2 hover:bg-surface-3"
                aria-label="Add tag"
              >
                <Plus className="size-4" />
              </button>
            </form>
          )}
        </Section>

        <Section title="SLA">
          <SlaPanel sla={ticket.sla} />
        </Section>

        {ticket.csat && (
          <Section title="Customer rating">
            <CsatCard csat={ticket.csat} />
          </Section>
        )}
      </div>

      <div
        role="tabpanel"
        id="panel-customer"
        aria-labelledby="tab-customer"
        hidden={tab !== "customer"}
        className="space-y-4"
      >
        <Section title="Customer">
          <div className="flex items-center gap-3">
            <span className="grid size-10 flex-none place-items-center rounded-full bg-primary-soft text-sm font-bold text-primary-ink">
              {initials(ticket.contact?.name ?? "C")}
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate font-semibold">{ticket.contact?.name}</div>
              <div className="truncate text-sm text-muted-foreground">{ticket.contact?.email}</div>
            </div>
            {ticket.contact?.vip && <VipBadge />}
          </div>
          <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 border-t border-border pt-3 text-sm">
            {ticket.contact?.customer_since && (
              <>
                <dt className="text-muted-foreground">Customer since</dt>
                <dd className="text-right">{formatDateTime(ticket.contact?.customer_since)}</dd>
              </>
            )}
            {ticket.contact?.phone && (
              <>
                <dt className="text-muted-foreground">Phone</dt>
                <dd className="text-right">
                  <a
                    href={`tel:${ticket.contact.phone}`}
                    className="text-primary-ink hover:underline"
                  >
                    {ticket.contact.phone}
                  </a>
                </dd>
              </>
            )}
            {ticket.contact?.organization && (
              <>
                <dt className="text-muted-foreground">Organization</dt>
                <dd className="text-right">{ticket.contact.organization}</dd>
              </>
            )}
            {!!ticket.contact?.csat_count && (
              <>
                <dt className="text-muted-foreground">Satisfaction</dt>
                <dd className="flex justify-end">
                  <CsatAverage
                    average={ticket.contact.csat_average}
                    count={ticket.contact.csat_count}
                  />
                </dd>
              </>
            )}
            {ticket.contact?.other_tickets != null && (
              <>
                <dt className="text-muted-foreground">Other tickets</dt>
                <dd className="text-right">{ticket.contact?.other_tickets}</dd>
              </>
            )}
          </dl>
          {ticket.contact?.id && (
            <Link
              href={`/customers/${ticket.contact?.id}`}
              className="mt-3 inline-block text-sm font-medium text-primary-ink hover:underline"
            >
              View customer →
            </Link>
          )}
        </Section>
        {tab === "customer" && (
          <>
            <OrdersCard ticketId={ticket.id} />
            <PaymentsCard ticketId={ticket.id} />
          </>
        )}
      </div>

      <div
        role="tabpanel"
        id="panel-more"
        aria-labelledby="tab-more"
        hidden={tab !== "more"}
        className="space-y-4"
      >
        {/* Mounted only when opened, so these optional calls don't fire until needed. */}
        {tab === "more" && (
          <>
            <RelatedCard ticketId={ticket.id} canEdit={canEdit} />
            <ApprovalsCard ticketId={ticket.id} canEdit={canEdit} />
            <TasksCard ticketId={ticket.id} canEdit={canEdit} />
            <TimeCard ticketId={ticket.id} canEdit={canEdit} />
            <SideConversations ticketId={ticket.id} canEdit={canEdit} />
            <JiraCard ticketId={ticket.id} canEdit={canEdit} />
          </>
        )}
      </div>

      <div role="tabpanel" id="panel-files" aria-labelledby="tab-files" hidden={tab !== "files"}>
        <Files ticketId={ticket.id} canUpload={perms.editTicket} />
      </div>
    </div>
  );
}

function Files({ ticketId, canUpload }: { ticketId: string; canUpload: boolean }) {
  const files = useAttachments(ticketId);
  const upload = useUploadAttachment(ticketId);
  const input = useRef<HTMLInputElement>(null);

  return (
    <Section title="Files">
      {files.isPending ? (
        <Skeleton className="h-8" />
      ) : files.data?.length ? (
        <ul className="space-y-1.5">
          {files.data.map((f) => (
            <li key={f.id}>
              <FileChip file={f} className="w-full" />
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">No files yet.</p>
      )}
      {canUpload && (
        <>
          <input
            ref={input}
            type="file"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              if (file.size > 25 * 1024 * 1024)
                return toast.error("That file is larger than 25 MB.");
              upload.mutate(file, { onSuccess: () => toast.success("File uploaded") });
            }}
          />
          <button
            onClick={() => input.current?.click()}
            disabled={upload.isPending}
            className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-primary-ink hover:underline disabled:opacity-50"
          >
            <Upload className="size-4" />{" "}
            {upload.isPending ? "Uploading…" : "Upload file (max 25 MB)"}
          </button>
        </>
      )}
    </Section>
  );
}

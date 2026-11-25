"use client";

import { Unlink } from "lucide-react";
import { toast } from "sonner";

import {
  AddToggle,
  ExtraCard,
  Muted,
  QueryState,
  TicketLink,
  toastError,
  type ExtraProps,
} from "@/components/tickets/extras-shared";
import { useConfirm } from "@/components/ui/confirm";
import { Button, Input, Select } from "@/components/ui/primitives";
import {
  useCreateChild,
  useLinkTicket,
  useMergeTicket,
  useRelated,
  useUnlinkTicket,
  type TicketRef,
} from "@/hooks/use-ticket-extras";
import { useTeams } from "@/hooks/use-workspace";
import { bind, bindSelect, fieldError, useFormik, yup } from "@/lib/forms";

/** Accepts a ticket id or a pasted ticket page URL. */
function parseTicketId(raw: string) {
  const v = raw.trim();
  const m = v.match(/\/tickets\/([^/?#\s]+)/);
  return m?.[1] ? decodeURIComponent(m[1]) : v;
}

function Group({
  title,
  items,
  onUnlink,
}: {
  title: string;
  items: TicketRef[];
  onUnlink?: (id: string) => void;
}) {
  if (!items.length) return null;
  return (
    <div className="mb-2">
      <div className="px-2 text-xs font-medium text-muted-foreground">{title}</div>
      <ul>
        {items.map((t) => (
          <li key={t.id} className="flex items-center gap-1">
            <div className="min-w-0 flex-1">
              <TicketLink t={t} />
            </div>
            {onUnlink && (
              <button
                aria-label={`Remove link to ${t.label ?? t.id}`}
                className="grid size-7 flex-none place-items-center rounded-md text-muted-foreground hover:bg-surface-2 hover:text-foreground"
                onClick={() => onUnlink(t.id)}
              >
                <Unlink className="size-3.5" />
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function IdForm({
  placeholder,
  submit,
  pending,
  close,
}: {
  placeholder: string;
  submit: (id: string) => void;
  pending: boolean;
  close: () => void;
}) {
  const form = useFormik({
    initialValues: { v: "" },
    validationSchema: yup.object({
      v: yup
        .string()
        .required("Enter a ticket number or link.")
        .test(
          "ticket",
          "That doesn't look like a ticket number or link.",
          (x) => !!parseTicketId(x ?? ""),
        ),
    }),
    onSubmit: ({ v }) => {
      const id = parseTicketId(v);
      if (id) submit(id);
    },
  });
  const err = fieldError(form, "v");
  return (
    <form className="flex flex-wrap gap-2" onSubmit={form.handleSubmit} noValidate>
      <Input
        {...bind(form, "v")}
        placeholder={placeholder}
        aria-label={placeholder}
        className="h-9 min-w-0 flex-1"
      />
      <Button type="submit" size="sm" className="h-9" loading={pending}>
        Go
      </Button>
      <Button type="button" size="sm" variant="ghost" className="h-9" onClick={close}>
        Cancel
      </Button>
      {err && (
        <p role="alert" className="basis-full text-xs text-danger">
          {err}
        </p>
      )}
    </form>
  );
}

function ChildForm({ ticketId, close }: { ticketId: string; close: () => void }) {
  const teams = useTeams();
  const create = useCreateChild(ticketId);
  const form = useFormik({
    initialValues: { subject: "", team: "" },
    validationSchema: yup.object({
      subject: yup.string().trim().required("Add a subject.").max(200),
      team: yup.string().required("Choose a team."),
    }),
    onSubmit: (v) =>
      create.mutate(
        { subject: v.subject.trim(), team_id: v.team },
        {
          onSuccess: () => {
            toast.success("Child ticket created");
            close();
          },
          onError: toastError,
        },
      ),
  });
  const problem = fieldError(form, "subject") ?? fieldError(form, "team");
  return (
    <form className="space-y-2" onSubmit={form.handleSubmit} noValidate>
      <Input
        {...bind(form, "subject")}
        placeholder="Child ticket subject"
        aria-label="Child ticket subject"
        maxLength={200}
        className="h-9"
      />
      <Select {...bindSelect(form, "team")} aria-label="Team">
        <option value="">Choose a team</option>
        {teams.data?.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </Select>
      {problem && (
        <p role="alert" className="text-xs text-danger">
          {problem}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" loading={create.isPending}>
          Create child
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={close}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

export function RelatedCard({ ticketId, canEdit }: ExtraProps) {
  const q = useRelated(ticketId);
  const merge = useMergeTicket(ticketId);
  const link = useLinkTicket(ticketId);
  const unlink = useUnlinkTicket(ticketId);
  const { ask, dialog } = useConfirm();
  const r = q.data;
  const empty = r && !r.parent && !r.children.length && !r.links.length && !r.merges.length;

  return (
    <ExtraCard title="Related">
      {dialog}
      <QueryState q={q} />
      {r && (
        <>
          {empty && <Muted className="mb-2">No related tickets.</Muted>}
          <Group title="Parent" items={r.parent ? [r.parent] : []} />
          <Group title="Child tickets" items={r.children} />
          <Group
            title="Linked"
            items={r.links}
            onUnlink={
              canEdit
                ? (id) =>
                    ask({
                      title: "Remove this link?",
                      message: "The two tickets are no longer linked.",
                      detail: id,
                      confirmLabel: "Remove link",
                      run: () => unlink.mutate(id, { onError: toastError }),
                    })
                : undefined
            }
          />
          <Group title="Merged in" items={r.merges} />
          <div className="mt-2 flex flex-wrap gap-2">
            <AddToggle label="Link" disabled={!canEdit}>
              {(close) => (
                <IdForm
                  placeholder="Ticket ID to link"
                  pending={link.isPending}
                  close={close}
                  submit={(id) =>
                    link.mutate(
                      { other_id: id },
                      {
                        onSuccess: () => {
                          toast.success("Tickets linked");
                          close();
                        },
                        onError: toastError,
                      },
                    )
                  }
                />
              )}
            </AddToggle>
            <AddToggle label="Merge into…" disabled={!canEdit}>
              {(close) => (
                <IdForm
                  placeholder="Open ticket ID to merge into"
                  pending={merge.isPending}
                  close={close}
                  submit={(id) =>
                    ask({
                      title: "Merge this ticket?",
                      message: "It is merged into the other ticket and can't be split apart again.",
                      detail: `Into ${id}`,
                      confirmLabel: "Merge",
                      tone: "warning",
                      run: () =>
                        merge.mutate(
                          { into_id: id },
                          {
                            onSuccess: () => {
                              toast.success("Ticket merged");
                              close();
                            },
                            onError: toastError,
                          },
                        ),
                    })
                  }
                />
              )}
            </AddToggle>
            <AddToggle label="Child ticket" disabled={!canEdit}>
              {(close) => <ChildForm ticketId={ticketId} close={close} />}
            </AddToggle>
          </div>
        </>
      )}
    </ExtraCard>
  );
}

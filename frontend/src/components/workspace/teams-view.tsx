"use client";

import { Pencil, Plus, Trash2, UsersRound } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
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
import { useAgents, useDeleteTeam, useSaveTeam, useTeams } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { Team } from "@/lib/api/types";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";

const teamSchema = yup.object({
  name: yup.string().trim().required("Enter a name").max(120, "Use at most 120 characters"),
  max_open_per_agent: yup
    .number()
    .typeError("Use a number from 1 to 50")
    .integer("Use a number from 1 to 50")
    .min(1, "Use a number from 1 to 50")
    .max(50, "Use a number from 1 to 50")
    .required("Use a number from 1 to 50"),
});

export function TeamsView() {
  const teams = useTeams();
  const del = useDeleteTeam();
  const { ask, dialog } = useConfirm();
  const [editing, setEditing] = useState<Team | "new" | null>(null);

  return (
    <div>
      {dialog}
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Teams decide who auto-assigned tickets go to (round-robin).
        </p>
        <Button onClick={() => setEditing("new")}>
          <Plus className="size-4" /> Add team
        </Button>
      </div>

      {teams.isPending ? (
        <Skeleton className="h-32" />
      ) : teams.isError ? (
        <EmptyState title="We couldn't load teams">
          {teams.error instanceof ApiError ? teams.error.userMessage : ""}
        </EmptyState>
      ) : teams.data.length === 0 ? (
        <Card>
          <EmptyState title="No teams yet">Create a team to start routing tickets.</EmptyState>
        </Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {teams.data.map((t) => (
            <Card key={t.id} className="p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 items-start gap-3">
                  <span
                    aria-hidden
                    className="grid size-10 flex-none place-items-center rounded-xl bg-primary-soft text-primary-ink"
                  >
                    <UsersRound className="size-5" />
                  </span>
                  <div className="min-w-0">
                    <h3 className="truncate font-semibold">{t.name}</h3>
                    <p className="text-sm text-muted-foreground">
                      {t.member_ids.length} members · max {t.max_open_per_agent} open each
                    </p>
                  </div>
                </div>
                <Badge tone={t.auto_assign ? "success" : "neutral"}>
                  {t.auto_assign ? "Auto-assign" : "Manual"}
                </Badge>
              </div>
              <div className="mt-3 flex gap-1.5">
                <Button size="sm" variant="outline" onClick={() => setEditing(t)}>
                  <Pencil className="size-3.5" /> Edit
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={del.isPending}
                  onClick={() =>
                    ask({
                      title: "Delete this team?",
                      message: "Tickets routed to it will no longer be auto-assigned.",
                      detail: t.name,
                      confirmLabel: "Delete",
                      run: () =>
                        del.mutate(t.id, { onSuccess: () => toast.success("Team deleted") }),
                    })
                  }
                >
                  <Trash2 className="size-3.5" /> Delete
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <TeamDialog team={editing} onClose={() => setEditing(null)} />
    </div>
  );
}

function TeamDialog({ team, onClose }: { team: Team | "new" | null; onClose: () => void }) {
  return (
    <Dialog
      open={team !== null}
      onClose={onClose}
      title={team === "new" ? "Add team" : "Edit team"}
    >
      {team !== null && (
        <TeamForm
          key={team === "new" ? "new" : team.id}
          team={team === "new" ? null : team}
          onClose={onClose}
        />
      )}
    </Dialog>
  );
}

function TeamForm({ team, onClose }: { team: Team | null; onClose: () => void }) {
  const save = useSaveTeam();
  const agents = useAgents();
  // GET /agents lists everyone (the status screen needs that); a team can only
  // hold active members, so the picker offers just those.
  const active = (agents.data ?? []).filter((a) => a.is_active !== false);
  const [srv, setSrv] = useState<Record<string, string>>({});
  const form = useFormik({
    initialValues: {
      name: team?.name ?? "",
      auto_assign: team?.auto_assign ?? true,
      max_open_per_agent: String(team?.max_open_per_agent ?? 5),
      member_ids: team?.member_ids ?? [],
    },
    validationSchema: teamSchema,
    onSubmit: (v) => {
      setSrv({});
      save.mutate(
        {
          id: team?.id,
          name: v.name.trim(),
          auto_assign: v.auto_assign,
          max_open_per_agent: Number(v.max_open_per_agent),
          // A member deactivated since the team was saved is still in
          // `team.member_ids`; sending them back would be refused.
          member_ids: agents.data
            ? v.member_ids.filter((id) => active.some((a) => a.member_id === id))
            : v.member_ids,
        },
        {
          onSuccess: () => {
            toast.success("Team saved");
            onClose();
          },
          onError: (e) => {
            if (e instanceof ApiError && e.status === 422)
              setSrv(Object.fromEntries(e.details.map((d) => [d.field, d.message])));
          },
        },
      );
    },
  });
  const members = form.values.member_ids;

  return (
    <form noValidate className="space-y-4" onSubmit={form.handleSubmit}>
      <Field label="Name" htmlFor="t-name" error={fieldError(form, "name") ?? srv.name}>
        <Input id="t-name" {...bind(form, "name")} />
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          name="auto_assign"
          checked={form.values.auto_assign}
          onChange={form.handleChange}
          className="size-4 accent-[var(--primary)]"
        />
        Auto-assign new tickets (round-robin)
      </label>
      <Field
        label="Max open tickets per agent"
        htmlFor="t-max"
        error={fieldError(form, "max_open_per_agent") ?? srv.max_open_per_agent ?? srv.max}
      >
        <Input id="t-max" type="number" min={1} max={50} {...bind(form, "max_open_per_agent")} />
      </Field>
      <fieldset>
        <legend className="mb-1.5 text-[13px] font-medium text-foreground-2">Members</legend>
        <div className="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
          {active.map((a) => (
            <label
              key={a.member_id}
              className="flex items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-surface-2"
            >
              <input
                type="checkbox"
                className="size-4 accent-[var(--primary)]"
                checked={members.includes(a.member_id)}
                onChange={(e) =>
                  form.setFieldValue(
                    "member_ids",
                    e.target.checked
                      ? [...members, a.member_id]
                      : members.filter((x) => x !== a.member_id),
                  )
                }
              />
              {a.name}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          Save team
        </Button>
      </div>
    </form>
  );
}

"use client";

import { Pencil, Plus, ShieldCheck, Trash2 } from "lucide-react";
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
  Select,
  Skeleton,
} from "@/components/ui/primitives";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import {
  DEFAULT_ROLE_RIGHTS,
  ROLE_RIGHTS,
  ROLE_SCOPES,
  rightsHeld,
  useDeleteRole,
  useRoles,
  useSaveRole,
  useTeams,
} from "@/hooks/use-workspace";
import type { CustomRole, RoleRightKey, RoleRights } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";

const scopeLabel = (s?: string) => ROLE_SCOPES.find((x) => x.value === s)?.label ?? s ?? "";

export function RolesView() {
  const roles = useRoles();
  const teams = useTeams();
  const del = useDeleteRole();
  const { ask, dialog } = useConfirm();
  const [editing, setEditing] = useState<CustomRole | "new" | null>(null);

  return (
    <div>
      {dialog}
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Custom roles sit beside Admin, Supervisor, Agent and Light agent. Changes reach members
          straight away.
        </p>
        <Button onClick={() => setEditing("new")}>
          <Plus className="size-4" /> Add role
        </Button>
      </div>

      {roles.isPending ? (
        <Skeleton className="h-32" />
      ) : roles.isError ? (
        <LoadError error={roles.error} />
      ) : roles.data.length === 0 ? (
        <Card>
          <EmptyState icon={<ShieldCheck className="size-6" />} title="No custom roles yet">
            Make a role when the built-in ones don&apos;t fit, like a billing team that only reads
            tickets.
          </EmptyState>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-border">
            {roles.data.map((r) => {
              const team = (teams.data ?? []).find((t) => t.id === r.team_id);
              return (
                <li key={r.id} className="flex flex-wrap items-start gap-3 px-4 py-3">
                  <div className="min-w-0 flex-1 basis-60">
                    <div className="text-sm font-semibold">{r.name}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      <Badge tone="primary">
                        {scopeLabel(r.scope)}
                        {team ? `: ${team.name}` : ""}
                      </Badge>
                      <Badge>{rightsHeld(r)} rights</Badge>
                      {typeof r.member_count === "number" && (
                        <Badge>
                          {r.member_count} member{r.member_count === 1 ? "" : "s"}
                        </Badge>
                      )}
                    </div>
                  </div>
                  <Button size="sm" variant="outline" onClick={() => setEditing(r)}>
                    <Pencil className="size-3.5" /> Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={del.isPending}
                    onClick={() =>
                      ask({
                        title: "Delete this role?",
                        message:
                          "Members must be moved to another role first. This can't be undone.",
                        detail: r.name,
                        confirmLabel: "Delete",
                        run: () =>
                          del.mutate(r.id, {
                            onSuccess: () => toast.success("Role deleted"),
                            onError: (e) =>
                              toast.error(
                                e instanceof ApiError
                                  ? e.userMessage
                                  : "Couldn't delete the role. Move its members to another role first.",
                              ),
                          }),
                      })
                    }
                  >
                    <Trash2 className="size-3.5" /> Delete
                  </Button>
                </li>
              );
            })}
          </ul>
        </Card>
      )}

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "Add role" : "Edit role"}
      >
        {editing !== null && (
          <RoleForm
            key={editing === "new" ? "new" : editing.id}
            role={editing === "new" ? null : editing}
            onClose={() => setEditing(null)}
          />
        )}
      </Dialog>
    </div>
  );
}

function RoleForm({ role, onClose }: { role: CustomRole | null; onClose: () => void }) {
  const save = useSaveRole();
  const teams = useTeams();
  const [name, setName] = useState(role?.name ?? "");
  const [rights, setRights] = useState<RoleRights>(
    role
      ? {
          reply: role.reply,
          assign_any: role.assign_any,
          reports: role.reports,
          settings: role.settings,
          approve: role.approve,
        }
      : DEFAULT_ROLE_RIGHTS,
  );
  const [scope, setScope] = useState(role?.scope ?? "all");
  const [teamId, setTeamId] = useState(role?.team_id ?? "");
  const [errs, setErrs] = useState<Record<string, string>>({});

  const needsTeam = scope !== "all";
  const toggle = (k: RoleRightKey) => setRights((r) => ({ ...r, [k]: !r[k] }));

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const next: Record<string, string> = {};
        if (!name.trim()) next.name = "Enter a name";
        if (needsTeam && !teamId) next.team_id = "Choose the team this role belongs to";
        setErrs(next);
        if (Object.keys(next).length) return;
        save.mutate(
          {
            id: role?.id,
            name: name.trim(),
            ...rights,
            scope,
            team_id: needsTeam ? teamId : null,
          },
          {
            onSuccess: () => {
              toast.success("Role saved");
              onClose();
            },
            onError: (err) =>
              setErrs(
                err instanceof ApiError && err.status !== 422
                  ? { _form: err.userMessage }
                  : fieldErrors(err),
              ),
          },
        );
      }}
    >
      <Field label="Name" htmlFor="role-name" error={errs.name}>
        <Input
          id="role-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={60}
        />
      </Field>

      <fieldset className="space-y-1.5">
        <legend className="text-[13px] font-medium text-foreground-2">Rights</legend>
        <div className="grid gap-2">
          {ROLE_RIGHTS.map((a) => (
            <div key={a.key} className="flex items-start gap-2 text-sm">
              <input
                id={`role-right-${a.key}`}
                type="checkbox"
                className="mt-0.5 size-4 accent-[var(--primary)]"
                checked={rights[a.key]}
                aria-describedby={"hint" in a ? `role-right-${a.key}-hint` : undefined}
                onChange={() => toggle(a.key)}
              />
              <div>
                <label htmlFor={`role-right-${a.key}`}>{a.label}</label>
                {"hint" in a && (
                  <span
                    id={`role-right-${a.key}-hint`}
                    className="block text-xs text-muted-foreground"
                  >
                    {a.hint}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          Managing members, roles and security stays with Admins.
        </p>
        {errs.actions && (
          <p role="alert" className="text-xs text-danger">
            {errs.actions}
          </p>
        )}
      </fieldset>

      <Field label="Which tickets" htmlFor="role-scope" error={errs.scope}>
        <Select
          id="role-scope"
          value={scope}
          options={ROLE_SCOPES.map((s) => ({ value: s.value, label: s.label }))}
          onChange={(e) => setScope(e.target.value)}
        />
      </Field>
      {needsTeam && (
        <Field
          label="Team"
          htmlFor="role-team"
          error={errs.team_id}
          hint="The team whose tickets this role can work on."
        >
          <Select
            id="role-team"
            placeholder="Choose a team"
            value={teamId}
            options={(teams.data ?? []).map((t) => ({ value: t.id, label: t.name }))}
            onChange={(e) => setTeamId(e.target.value)}
          />
        </Field>
      )}

      <FormError message={errs._form} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          Save
        </Button>
      </div>
    </form>
  );
}

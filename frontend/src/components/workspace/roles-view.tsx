"use client";

import { Pencil, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

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
  ROLE_ACTIONS,
  ROLE_SCOPES,
  useDeleteRole,
  useRoles,
  useSaveRole,
  useTeams,
} from "@/hooks/use-workspace";
import type { CustomRole } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";

const scopeLabel = (s?: string) => ROLE_SCOPES.find((x) => x.value === s)?.label ?? s ?? "";

export function RolesView() {
  const roles = useRoles();
  const teams = useTeams();
  const del = useDeleteRole();
  const [editing, setEditing] = useState<CustomRole | "new" | null>(null);

  return (
    <div>
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
                      <Badge>{(r.actions ?? []).length} rights</Badge>
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
                    onClick={() => {
                      if (confirm(`Delete the role “${r.name}”?`))
                        del.mutate(r.id, {
                          onSuccess: () => toast.success("Role deleted"),
                          onError: (e) =>
                            toast.error(
                              e instanceof ApiError
                                ? e.userMessage
                                : "Couldn't delete the role. Move its members to another role first.",
                            ),
                        });
                    }}
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
  const [actions, setActions] = useState<string[]>(role?.actions ?? ["read_ticket"]);
  const [scope, setScope] = useState(role?.scope ?? "all");
  const [teamId, setTeamId] = useState(role?.team_id ?? "");
  const [errs, setErrs] = useState<Record<string, string>>({});

  const needsTeam = scope !== "all";
  const toggle = (k: string) =>
    setActions((a) => (a.includes(k) ? a.filter((x) => x !== k) : [...a, k]));

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
            actions,
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
        <div className="grid gap-1.5 sm:grid-cols-2">
          {ROLE_ACTIONS.map((a) => (
            <label key={a.key} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-[var(--primary)]"
                checked={actions.includes(a.key)}
                onChange={() => toggle(a.key)}
              />
              {a.label}
            </label>
          ))}
        </div>
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

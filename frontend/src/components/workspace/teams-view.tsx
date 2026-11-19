"use client";

import { Pencil, Plus, Trash2 } from "lucide-react";
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
  Skeleton,
} from "@/components/ui/primitives";
import { useAgents, useDeleteTeam, useSaveTeam, useTeams } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { Team } from "@/lib/api/types";

export function TeamsView() {
  const teams = useTeams();
  const del = useDeleteTeam();
  const [editing, setEditing] = useState<Team | "new" | null>(null);

  return (
    <div>
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
                <div>
                  <h3 className="font-semibold">{t.name}</h3>
                  <p className="text-sm text-muted-foreground">
                    {t.member_ids.length} members · max {t.max_open_per_agent} open each
                  </p>
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
                  onClick={() => {
                    if (confirm(`Delete team “${t.name}”?`))
                      del.mutate(t.id, { onSuccess: () => toast.success("Team deleted") });
                  }}
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
  const [name, setName] = useState(team?.name ?? "");
  const [auto, setAuto] = useState(team?.auto_assign ?? true);
  const [max, setMax] = useState(String(team?.max_open_per_agent ?? 5));
  const [members, setMembers] = useState<string[]>(team?.member_ids ?? []);
  const [errs, setErrs] = useState<Record<string, string>>({});

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const n = Number(max);
        const next: Record<string, string> = {};
        if (!name.trim()) next.name = "Enter a name";
        if (!Number.isInteger(n) || n < 1 || n > 50) next.max = "Use a number from 1 to 50";
        setErrs(next);
        if (Object.keys(next).length) return;
        save.mutate(
          {
            id: team?.id,
            name: name.trim(),
            auto_assign: auto,
            max_open_per_agent: n,
            // A member deactivated since the team was saved is still in
            // `team.member_ids`; sending them back would be refused.
            member_ids: agents.data
              ? members.filter((id) => active.some((a) => a.member_id === id))
              : members,
          },
          {
            onSuccess: () => {
              toast.success("Team saved");
              onClose();
            },
            onError: (e) => {
              if (e instanceof ApiError && e.status === 422)
                setErrs(Object.fromEntries(e.details.map((d) => [d.field, d.message])));
            },
          },
        );
      }}
    >
      <Field label="Name" htmlFor="t-name" error={errs.name}>
        <Input id="t-name" value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={auto}
          onChange={(e) => setAuto(e.target.checked)}
          className="size-4 accent-[var(--primary)]"
        />
        Auto-assign new tickets (round-robin)
      </label>
      <Field
        label="Max open tickets per agent"
        htmlFor="t-max"
        error={errs.max ?? errs.max_open_per_agent}
      >
        <Input
          id="t-max"
          type="number"
          min={1}
          max={50}
          value={max}
          onChange={(e) => setMax(e.target.value)}
        />
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
                  setMembers((m) =>
                    e.target.checked ? [...m, a.member_id] : m.filter((x) => x !== a.member_id),
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

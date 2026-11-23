"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";

/**
 * The five rights a custom role can hold, the same names the API takes (`RoleBody`). What
 * else a member can do comes with `reply`, or stays with Admins: members, roles and
 * security are never given by a custom role.
 */
export const ROLE_RIGHTS = [
  {
    key: "reply",
    label: "Reply to customers",
    hint: "Reply, use macros, accept chats, resolve, create tickets, assign to themselves.",
  },
  { key: "assign_any", label: "Assign tickets to anyone" },
  { key: "reports", label: "View reports" },
  {
    key: "settings",
    label: "Change workspace settings",
    hint: "Teams, ticket types, rules, macros, SLA, email. Never members, roles or security.",
  },
  { key: "approve", label: "Approve requests" },
] as const;

export type RoleRightKey = (typeof ROLE_RIGHTS)[number]["key"];
export type RoleRights = Record<RoleRightKey, boolean>;

/** A new role can reply and nothing more, as the API's defaults say. */
export const DEFAULT_ROLE_RIGHTS: RoleRights = {
  reply: true,
  assign_any: false,
  reports: false,
  settings: false,
  approve: false,
};

export const ROLE_SCOPES = [
  { value: "all", label: "All tickets" },
  { value: "team_edit", label: "Own team edits, other teams view only" },
  { value: "team_only", label: "Own team only (other teams' tickets are hidden)" },
] as const;

export interface CustomRole extends RoleRights {
  id: string;
  name: string;
  scope?: string;
  team_id?: string | null;
  team_name?: string | null;
  member_count?: number;
}

export interface RoleInput extends RoleRights {
  name: string;
  scope: string;
  team_id: string | null;
}

/** How many of the five rights a role holds (the list shows it as "N rights"). */
export const rightsHeld = (role: Partial<RoleRights>): number =>
  ROLE_RIGHTS.filter((r) => role[r.key] === true).length;

export const rolesKey = ["roles"] as const;

export function useRoles() {
  return useQuery({
    queryKey: rolesKey,
    queryFn: () => api.get<unknown>("roles"),
    select: listOf<CustomRole>,
  });
}

export function useSaveRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: RoleInput & { id?: string }) =>
      id ? api.put<CustomRole>(`roles/${id}`, body) : api.post<CustomRole>("roles", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: rolesKey }),
  });
}

export function useDeleteRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`roles/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: rolesKey }),
  });
}

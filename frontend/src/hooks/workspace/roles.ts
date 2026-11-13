"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";

export const ROLE_ACTIONS = [
  { key: "read_ticket", label: "Read tickets" },
  { key: "create_ticket", label: "Create tickets" },
  { key: "reply", label: "Reply to customers" },
  { key: "private_note", label: "Add private notes" },
  { key: "change_fields", label: "Change ticket fields" },
  { key: "assign_self", label: "Assign to themselves" },
  { key: "assign_others", label: "Assign to others" },
  { key: "approve", label: "Approve requests" },
  { key: "reports", label: "View reports" },
  { key: "settings", label: "Change settings" },
  { key: "list_members", label: "See the member list" },
  { key: "manage_access", label: "Manage members and roles" },
] as const;

export const ROLE_SCOPES = [
  { value: "all", label: "All tickets" },
  { value: "team_edit", label: "Own team edits, other teams view only" },
  { value: "team_only", label: "Own team only (other teams' tickets are hidden)" },
] as const;

export interface CustomRole {
  id: string;
  name: string;
  actions?: string[];
  scope?: string;
  team_id?: string | null;
  member_count?: number;
}

export interface RoleInput {
  name: string;
  actions: string[];
  scope: string;
  team_id: string | null;
}

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

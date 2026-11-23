"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

export type SandboxKind = "rules" | "macros" | "saved_replies";

/** Items are saved as a whole list: keep `id` = change it, no `id` = new, left out = removed. */
export interface SandboxRule {
  id?: string;
  name: string;
  event: string;
  enabled: boolean;
  conditions: Record<string, unknown>[];
  actions: Record<string, unknown>[];
  problems?: string[];
}
export interface SandboxMacro {
  id?: string;
  name: string;
  steps: Record<string, unknown>[];
  problems?: string[];
}
export interface SandboxReply {
  id?: string;
  shortcut: string;
  body: string;
}

export interface Sandbox {
  rules: SandboxRule[];
  macros: SandboxMacro[];
  saved_replies: SandboxReply[];
  created_at: string;
  updated_at: string;
  updated_by_name?: string | null;
}

export interface TrySide {
  type?: string | null;
  team?: string | null;
  priority?: string | null;
  status?: string | null;
  tags?: string[];
  lines?: string[];
}
export interface TryRow {
  sample: string;
  text?: string;
  channel?: string;
  live: TrySide;
  sandbox: TrySide;
  same: boolean;
}

export interface CompareSection {
  added: unknown[];
  changed: { id?: string; label?: string; changes?: unknown }[];
  removed: unknown[];
  order_changed: boolean;
}
export interface SandboxCompare {
  rules: CompareSection;
  macros: CompareSection;
  saved_replies: CompareSection;
  same: boolean;
}

const key = ["sandbox"] as const;

/** `null` when there is no sandbox yet (404). */
export function useSandbox() {
  return useQuery({
    queryKey: key,
    queryFn: async ({ signal }): Promise<Sandbox | null> => {
      try {
        return await api.get<Sandbox>("sandbox", undefined, signal);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
  });
}

export function useCreateSandbox() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: () => api.post<Sandbox>("sandbox"),
    onSuccess: (d) => qc.setQueryData(key, d),
  });
}

export function useDeleteSandbox() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<void>("sandbox"),
    onSuccess: () => {
      qc.setQueryData(key, null);
      qc.removeQueries({ queryKey: ["sandbox", "compare"] });
    },
  });
}

export function useSaveSandboxList() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: ({ kind, items }: { kind: SandboxKind; items: unknown[] }) =>
      api.put<Sandbox>(`sandbox/${kind}`, { items }),
    onSuccess: (d) => {
      qc.setQueryData(key, d);
      qc.invalidateQueries({ queryKey: ["sandbox", "compare"] });
    },
  });
}

export function useTrySandbox() {
  return useMutation({
    meta: { silent: true },
    mutationFn: () => api.post<TryRow[]>("sandbox/try"),
  });
}

export function useSandboxCompare(enabled: boolean) {
  return useQuery({
    queryKey: ["sandbox", "compare"],
    enabled,
    queryFn: ({ signal }) => api.get<SandboxCompare>("sandbox/compare", undefined, signal),
  });
}

/** All or nothing. 422: nothing to deploy, or an item no longer fits. */
export function useDeploySandbox() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: () => api.post<unknown>("sandbox/deploy"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sandbox"] });
      qc.invalidateQueries({ queryKey: queryKeys.rules.all });
      qc.invalidateQueries({ queryKey: queryKeys.macros.all });
      qc.invalidateQueries({ queryKey: queryKeys.savedReplies.all });
    },
  });
}

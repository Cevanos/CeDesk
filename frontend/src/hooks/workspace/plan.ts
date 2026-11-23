"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";

export interface RetentionSettings {
  /** Delete resolved tickets after N days; null keeps them. */
  closed_ticket_days: number | null;
  /** The most days of history the plan allows; null when it has no limit. */
  plan_history_days: number | null;
  effective_days: number | null;
  export_before_delete: boolean;
  updated_by_name?: string | null;
  updated_at?: string | null;
}

export type RetentionInput = Partial<
  Pick<RetentionSettings, "closed_ticket_days" | "export_before_delete">
>;

export interface ArchiveSettings {
  /** 30 to 3650, or null for the default (120). */
  after_days: number | null;
  effective_days: number;
  updated_by_name?: string | null;
  updated_at?: string | null;
}

/** A `null` limit means "no limit"; `plan: null` means "No plan limits". */
export interface Usage {
  plan: string | null;
  agents: number;
  tickets_this_month: number;
  storage_bytes: number;
  limits: {
    agents: number | null;
    tickets_month: number | null;
    storage_bytes: number | null;
  };
  retention_days: number | null;
}

export interface RetentionExport {
  id: string;
  tickets: number;
  size: number;
  created_at: string;
  expires_at: string;
}

export function useUsage() {
  return useQuery({
    queryKey: ["plan", "usage"],
    queryFn: ({ signal }) => api.get<Usage>("usage", undefined, signal),
  });
}

export function useRetention() {
  return useQuery({
    queryKey: ["plan", "retention"],
    queryFn: ({ signal }) => api.get<RetentionSettings>("retention", undefined, signal),
  });
}

export function useSaveRetention() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RetentionInput) => api.put<RetentionSettings>("retention", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plan"] }),
  });
}

export function useArchiveSettings() {
  return useQuery({
    queryKey: ["plan", "archive"],
    queryFn: ({ signal }) => api.get<ArchiveSettings>("archive/settings", undefined, signal),
  });
}

export function useSaveArchiveSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { after_days: number | null }) =>
      api.put<ArchiveSettings>("archive/settings", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plan", "archive"] }),
  });
}

/** Files kept from "Export before delete", newest first. */
export function useRetentionExports() {
  return useQuery({
    queryKey: ["plan", "retention-exports"],
    queryFn: ({ signal }) => api.get<unknown>("retention/exports", undefined, signal),
    select: listOf<RetentionExport>,
  });
}

export function retentionExportPath(id: string) {
  return `retention/exports/${encodeURIComponent(id)}/file`;
}

/** Parse a whole-number day count; null when blank or invalid. */
export function parseDays(s: string): number | null {
  const t = s.trim();
  if (!/^\d+$/.test(t)) return null;
  return Number(t);
}

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";

export interface RetentionSettings {
  /** Delete resolved tickets after N days; null/0 keeps them. */
  resolved_ticket_days?: number | null;
  [key: string]: unknown;
}

export interface ArchiveSettings {
  archive_age_days?: number;
  [key: string]: unknown;
}

export interface UsageMetric {
  key?: string;
  label?: string;
  used?: number;
  limit?: number | null;
}

export interface Usage {
  plan?: string;
  metrics?: UsageMetric[];
  [key: string]: unknown;
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
    mutationFn: (body: RetentionSettings) => api.put<RetentionSettings>("retention", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plan", "retention"] }),
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
    mutationFn: (body: ArchiveSettings) => api.put<ArchiveSettings>("archive/settings", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plan", "archive"] }),
  });
}

/** Normalise usage metrics from a `metrics` array or a bare { key: {used, limit} } map. */
export function usageRows(u: Usage | undefined): UsageMetric[] {
  if (!u) return [];
  if (Array.isArray(u.metrics)) return u.metrics;
  const rows: UsageMetric[] = [];
  for (const [key, v] of Object.entries(u)) {
    if (v && typeof v === "object" && !Array.isArray(v) && "used" in v) {
      rows.push({ key, label: key.replace(/_/g, " "), ...(v as UsageMetric) });
    }
  }
  return rows;
}

/** Parse a whole-number day count; null when blank or invalid. */
export function parseDays(s: string): number | null {
  const t = s.trim();
  if (!/^\d+$/.test(t)) return null;
  return Number(t);
}

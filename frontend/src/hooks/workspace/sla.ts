"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { Holiday, SlaPolicy, SlaSettings, SlaTarget } from "@/lib/api/types";

/** Everyone can read the SLA settings; only Admin can change them. */
export function useSla() {
  return useQuery({
    queryKey: queryKeys.sla.all,
    queryFn: () => api.get<SlaSettings>("sla"),
    staleTime: 60_000,
  });
}

export function useSaveSlaTargets() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (
      targets: Pick<SlaTarget, "priority" | "first_reply_minutes" | "resolve_minutes">[],
    ) => api.put<SlaSettings>("sla/targets", { targets }),
    onSuccess: (data) => qc.setQueryData(queryKeys.sla.all, data),
  });
}

export type SlaPolicyInput = Pick<
  SlaPolicy,
  | "name"
  | "enabled"
  | "vip_only"
  | "channels"
  | "priorities"
  | "first_reply_minutes"
  | "resolve_minutes"
>;

/** The write endpoints answer with different shapes, so refetch instead of trusting them. */
function useRefreshSla() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: queryKeys.sla.all });
}

/** Replace the whole ordered list of policies. The last one is the catch-all. */
export function useSaveSlaPolicies() {
  const refresh = useRefreshSla();
  return useMutation({
    mutationFn: (policies: SlaPolicyInput[]) => api.put<unknown>("sla/policies", { policies }),
    onSuccess: refresh,
  });
}

/** Go back to the built-in policies. */
export function useResetSlaPolicies() {
  const refresh = useRefreshSla();
  return useMutation({
    mutationFn: () => api.put<unknown>("sla/policies", { reset: true }),
    onSuccess: refresh,
  });
}

export interface SlaCalendarInput {
  timezone: string;
  /** 0 = Monday ... 6 = Sunday */
  days: number[];
  /** "HH:MM" */
  opens: string;
  closes: string;
  always_open: boolean;
}

export function useSaveSlaCalendar() {
  const refresh = useRefreshSla();
  return useMutation({
    mutationFn: (body: SlaCalendarInput) => api.put<unknown>("sla/calendar", body),
    onSuccess: refresh,
  });
}

/** Add (no id) or edit (id) a holiday. */
export function useSaveHoliday() {
  const refresh = useRefreshSla();
  return useMutation({
    mutationFn: ({ id, ...body }: Pick<Holiday, "day" | "name"> & { id?: string }) =>
      id
        ? api.patch<unknown>(`holidays/${id}`, body).then((r) => r.data)
        : api.post<unknown>("holidays", body),
    onSuccess: refresh,
  });
}

export function useDeleteHoliday() {
  const refresh = useRefreshSla();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`holidays/${id}`),
    onSuccess: refresh,
  });
}

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { Rule } from "@/lib/api/types";

export function useRules() {
  return useQuery({
    queryKey: queryKeys.rules.all,
    queryFn: () => api.get<unknown>("rules"),
    select: listOf<Rule>,
    staleTime: 30_000,
  });
}

export type RuleInput = Pick<
  Rule,
  "name" | "event" | "conditions" | "actions" | "max_runs_per_hour" | "max_runs_per_day"
> & {
  enabled?: boolean;
};

export function useSaveRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: Partial<RuleInput> & { id?: string }) =>
      id ? api.patch<Rule>(`rules/${id}`, body).then((r) => r.data) : api.post<Rule>("rules", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.rules.all }),
  });
}

export function useDeleteRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`rules/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.rules.all }),
  });
}

export function useReorderRules() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids: string[]) => api.put<unknown>("rules/order", { ids }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.rules.all }),
  });
}

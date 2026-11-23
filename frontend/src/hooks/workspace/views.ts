"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { SavedView } from "@/lib/api/types";

/** Shared views first (A to Z), then the person's own. */
export function sortViews(views: SavedView[]): SavedView[] {
  return [...views].sort(
    (a, b) => Number(b.shared) - Number(a.shared) || a.name.localeCompare(b.name),
  );
}

export function useViews() {
  return useQuery({
    queryKey: queryKeys.views.all,
    queryFn: () => api.get<unknown>("views"),
    select: (d) => sortViews(listOf<SavedView>(d)),
    staleTime: 30_000,
  });
}

export function useCreateView() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { name: string; filters: Record<string, string>; shared: boolean }) =>
      api.post<SavedView>("views", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.views.all }),
  });
}

export function useUpdateView() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: ({
      id,
      ...patch
    }: {
      id: string;
      name?: string;
      filters?: Record<string, string>;
      shared?: boolean;
    }) => api.patch<SavedView>(`views/${id}`, patch).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.views.all }),
  });
}

export function useDeleteView() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (id: string) => api.delete<void>(`views/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.views.all }),
  });
}

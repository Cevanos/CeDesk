"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { SavedReply } from "@/lib/api/types";

/** `q` is the text typed after `/` in the reply box (empty lists them all). */
export function useSavedReplies(q = "", enabled = true) {
  return useQuery({
    queryKey: queryKeys.savedReplies.search(q),
    queryFn: ({ signal }) => api.get<unknown>("saved-replies", { q }, signal),
    select: listOf<SavedReply>,
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function useSaveSavedReply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id?: string; shortcut: string; body: string }) =>
      id
        ? api.patch<SavedReply>(`saved-replies/${id}`, body).then((r) => r.data)
        : api.post<SavedReply>("saved-replies", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.savedReplies.all }),
  });
}

export function useDeleteSavedReply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`saved-replies/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.savedReplies.all }),
  });
}

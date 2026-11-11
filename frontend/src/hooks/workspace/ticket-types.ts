"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { TicketType } from "@/lib/api/types";

export function useTicketTypes() {
  return useQuery({
    queryKey: queryKeys.ticketTypes.all,
    queryFn: () => api.get<unknown>("ticket-types"),
    select: listOf<TicketType>,
    staleTime: 60_000,
  });
}

export function useSaveTicketType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: Partial<Omit<TicketType, "id">> & { id?: string }) =>
      id
        ? api.patch(`ticket-types/${id}`, body).then((r) => r.data)
        : api.post("ticket-types", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.ticketTypes.all }),
  });
}

export function useDeleteTicketType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`ticket-types/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.ticketTypes.all }),
  });
}

export function useReorderTicketTypes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids: string[]) => api.put<void>("ticket-types/order", { ids }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.ticketTypes.all }),
  });
}

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, createIdempotency, listOf, request } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { Macro, Ticket } from "@/lib/api/types";

export function useMacros(enabled = true) {
  return useQuery({
    queryKey: queryKeys.macros.all,
    queryFn: () => api.get<unknown>("macros"),
    select: listOf<Macro>,
    enabled,
    staleTime: 30_000,
  });
}

export function useSaveMacro() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id?: string; name?: string; steps?: Macro["steps"] }) =>
      id
        ? api.patch<Macro>(`macros/${id}`, body).then((r) => r.data)
        : api.post<Macro>("macros", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.macros.all }),
  });
}

export function useDeleteMacro() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`macros/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.macros.all }),
  });
}

export function useReorderMacros() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids: string[]) => api.put<unknown>("macros/order", { ids }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.macros.all }),
  });
}

/** Run a macro on a ticket: `If-Match` + `Idempotency-Key` (guide 3.3, 3.2). */
export function useRunMacro(ticketId: string) {
  const qc = useQueryClient();
  const [runKey] = useState(createIdempotency);
  return useMutation({
    meta: { loud422: true },
    mutationFn: ({ macroId, etag }: { macroId: string; etag: string | null }) =>
      request<Ticket>(`tickets/${ticketId}/macros/${macroId}`, {
        method: "POST",
        headers: { ...runKey.header(macroId), ...(etag ? { "If-Match": etag } : {}) },
      }),
    onSuccess: () => runKey.done(),
    onError: (e) => {
      // A 409/422 means the request itself is stale: next click starts fresh.
      if ((e as { status?: number }).status === 409 || (e as { status?: number }).status === 422)
        runKey.done();
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: queryKeys.tickets.detail(ticketId) });
      qc.invalidateQueries({ queryKey: queryKeys.tickets.lists });
    },
  });
}

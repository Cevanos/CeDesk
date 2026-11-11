"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { EmailAddress, FailedEmail, FailedEvent } from "@/lib/api/types";

export function useEmailAddresses() {
  return useQuery({
    queryKey: queryKeys.emailAddresses.all,
    queryFn: () => api.get<unknown>("email-addresses"),
    select: listOf<EmailAddress>,
    staleTime: 30_000,
  });
}

export function useSaveEmailAddress() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      ...body
    }: {
      id?: string;
      address?: string;
      name?: string;
      signature?: string;
      is_default?: boolean;
    }) =>
      id
        ? api.patch<EmailAddress>(`email-addresses/${id}`, body).then((r) => r.data)
        : api.post<EmailAddress>("email-addresses", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.emailAddresses.all }),
  });
}

export function useDeleteEmailAddress() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`email-addresses/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.emailAddresses.all }),
  });
}

export function useSendTestEmail() {
  return useMutation({
    mutationFn: (id: string) => api.post<{ sent_to: string }>(`email-addresses/${id}/test`),
  });
}

/** Failed emails and failed events (Admin). */
export function useFailedEmails() {
  return useQuery({
    queryKey: queryKeys.failed.emails,
    queryFn: () => api.get<unknown>("email/failed", { limit: 100 }),
    select: listOf<FailedEmail>,
    staleTime: 15_000,
  });
}

export function useRetryEmail() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<void>(`email/${id}/retry`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.failed.emails }),
  });
}

export function useFailedEvents() {
  return useQuery({
    queryKey: queryKeys.failed.events,
    queryFn: () => api.get<unknown>("outbox/failed", { limit: 100 }),
    select: listOf<FailedEvent>,
    staleTime: 15_000,
  });
}

export function useRetryEvent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<void>(`outbox/${id}/retry`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.failed.events }),
  });
}

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { DnsState, EmailAddress, FailedEmail, FailedEvent } from "@/lib/api/types";

/** How long to keep polling for a forwarding check mail to come back. */
const FORWARDING_WAIT_MS = 120_000;

/**
 * The address list. While a forwarding check mail is out (status `waiting`) it re-reads
 * every 5 s so the badge flips by itself, and gives up after about two minutes.
 */
export function useEmailAddresses() {
  const waitingSince = useRef<number | null>(null);
  return useQuery({
    queryKey: queryKeys.emailAddresses.all,
    queryFn: () => api.get<unknown>("email-addresses"),
    select: listOf<EmailAddress>,
    staleTime: 30_000,
    refetchInterval: (q) => {
      const waiting = listOf<EmailAddress>(q.state.data).some(
        (a) => a.forwarding?.status === "waiting",
      );
      if (!waiting) {
        waitingSince.current = null;
        return false;
      }
      waitingSince.current ??= Date.now();
      return Date.now() - waitingSince.current < FORWARDING_WAIT_MS ? 5_000 : false;
    },
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
      dkim_selector?: string | null;
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

/** Sends a check mail to the address; the answer arrives later, so the UI polls. */
export function useForwardingCheck() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<unknown>(`email-addresses/${id}/forwarding-check`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.emailAddresses.all }),
  });
}

export function useDnsCheck() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<DnsState>(`email-addresses/${id}/dns-check`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.emailAddresses.all }),
  });
}

export function useSaveDkimSelector() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, dkim_selector }: { id: string; dkim_selector: string }) =>
      api.patch<EmailAddress>(`email-addresses/${id}`, { dkim_selector }).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.emailAddresses.all }),
  });
}

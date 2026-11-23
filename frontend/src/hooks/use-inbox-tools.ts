"use client";

import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import type { TicketWithEtag } from "@/hooks/use-tickets";
import { ApiError, api, request } from "@/lib/api/client";
import { apiMessage } from "@/lib/api/messages";
import { queryKeys } from "@/lib/api/query-keys";
import type {
  BulkAction,
  BulkResult,
  FollowerList,
  Ticket,
  TranscriptResult,
} from "@/lib/api/types";

/** A ticket answer replaces the cached detail (with its new ETag); lists reload. */
function storeTicket(qc: QueryClient, id: string, r: { data: Ticket; etag: string | null }) {
  qc.setQueryData<TicketWithEtag>(queryKeys.tickets.detail(id), { ticket: r.data, etag: r.etag });
  qc.invalidateQueries({ queryKey: queryKeys.tickets.lists });
  qc.invalidateQueries({ queryKey: queryKeys.tickets.followers(id) });
}

function failed(qc: QueryClient, id: string, e: unknown) {
  toast.error(apiMessage(e));
  // A 409 on If-Match means the copy on screen is stale.
  if (e instanceof ApiError && e.status === 409) {
    qc.invalidateQueries({ queryKey: queryKeys.tickets.detail(id) });
  }
}

/** Snooze, or move the time of a snooze. `until` is an ISO time with a zone. */
export function useSnoozeTicket(id: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: ({ until, etag }: { until: string; etag: string | null }) =>
      request<Ticket>(`tickets/${id}/snooze`, {
        method: "POST",
        body: { until },
        headers: etag ? { "If-Match": etag } : undefined,
      }),
    onSuccess: (r) => storeTicket(qc, id, r),
    onError: (e) => failed(qc, id, e),
  });
}

export function useUnsnoozeTicket(id: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: () => request<Ticket>(`tickets/${id}/snooze`, { method: "DELETE" }),
    onSuccess: (r) => storeTicket(qc, id, r),
    onError: (e) => failed(qc, id, e),
  });
}

export function useFollowers(id: string) {
  return useQuery({
    queryKey: queryKeys.tickets.followers(id),
    queryFn: () => api.get<FollowerList>(`tickets/${id}/followers`),
    staleTime: 15_000,
  });
}

/** Follow / add a follower (your own id to follow). The answer is the new list. */
export function useAddFollower(id: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (memberId: string) =>
      api.post<FollowerList>(`tickets/${id}/followers`, { member_id: memberId }),
    onSuccess: (list) => qc.setQueryData(queryKeys.tickets.followers(id), list),
    onError: (e) => toast.error(apiMessage(e)),
  });
}

/** Unfollow / remove a follower. The answer is the new list. */
export function useRemoveFollower(id: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (memberId: string) =>
      api.delete<FollowerList>(`tickets/${id}/followers/${encodeURIComponent(memberId)}`),
    onSuccess: (list) => qc.setQueryData(queryKeys.tickets.followers(id), list),
    onError: (e) => toast.error(apiMessage(e)),
  });
}

export const BULK_MAX = 100;

/** One change on up to 100 tickets. 200 even when some fail: read `results`. */
export function useBulkTickets() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { ticket_ids: string[] } & BulkAction) =>
      api.post<BulkResult>("tickets/bulk", v),
    onError: (e) => toast.error(apiMessage(e)),
    onSettled: () => qc.invalidateQueries({ queryKey: queryKeys.tickets.all }),
  });
}

/** Email the customer-visible conversation. 202: queued, sent within a minute. */
export function useSendTranscript(id: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (to?: string) =>
      api.post<TranscriptResult>(`tickets/${id}/transcript`, to ? { to } : {}),
    // The history gets a "Transcript emailed" line.
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tickets.detail(id) }),
  });
}

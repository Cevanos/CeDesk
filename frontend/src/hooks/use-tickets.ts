"use client";

import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useState } from "react";

import { applyTicketPatch, type TicketPatch } from "@/lib/api/apply-ticket-patch";
import { ApiError, api, createIdempotency, listOf, request, type Query } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import { ticketSummarySchema, validateList } from "@/lib/api/schemas";
import type { TicketFilters } from "@/lib/api/ticket-filters";
import type {
  Attachment,
  ContactTickets,
  MessagePage,
  Priority,
  Ticket,
  TicketList,
  TicketSummary,
} from "@/lib/api/types";
import { newId } from "@/lib/new-id";

export type { TicketFilters } from "@/lib/api/ticket-filters";
export type { TicketPatch } from "@/lib/api/apply-ticket-patch";

/** Cursor-paged ticket list Counts come with every page. */
export function useTickets(filters: TicketFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.tickets.list(filters),
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }): Promise<TicketList> => {
      const raw = await api.get<Record<string, unknown>>(
        "tickets",
        { ...filters, limit: 30, cursor: pageParam } satisfies Query,
        signal,
      );
      return {
        items: validateList(ticketSummarySchema, listOf<TicketSummary>(raw), "GET /tickets"),
        next_cursor: (raw?.next_cursor as string | null) ?? null,
        counts: (raw?.counts as TicketList["counts"]) ?? {},
      };
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    staleTime: 15_000,
    // No live push yet: refresh every 30 s while the tab is visible.
    refetchInterval: 30_000,
  });
}

export interface TicketWithEtag {
  ticket: Ticket;
  etag: string | null;
}

/** A ticket together with the ETag needed for `If-Match` edits */
export function useTicket(id: string) {
  return useQuery({
    queryKey: queryKeys.tickets.detail(id),
    queryFn: async (): Promise<TicketWithEtag> => {
      const r = await request<Ticket>(`tickets/${id}`);
      return { ticket: r.data, etag: r.etag };
    },
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
}

export function useOlderMessages(id: string) {
  return useMutation({
    mutationFn: (cursor: string) => api.get<MessagePage>(`tickets/${id}/messages`, { cursor }),
  });
}

export interface NewTicketInput {
  subject: string;
  contact: { name: string; email: string };
  description?: string;
  priority?: Priority;
  channel?: string;
  type_id?: string | null;
  team_id?: string | null;
  assignee_id?: string | null;
  fields?: Record<string, string | number>;
  tags?: string[];
}

/** A key is only reusable for the identical request: drop it once it can't be retried. */
function settleKey(idem: ReturnType<typeof createIdempotency>, e?: unknown) {
  if (!e || (e instanceof ApiError && [409, 413, 422].includes(e.status))) idem.done();
}

export function useCreateTicket() {
  const qc = useQueryClient();
  const [idem] = useState(createIdempotency);
  return useMutation({
    mutationFn: (v: NewTicketInput) =>
      api.post<Ticket>("tickets", v, idem.header(JSON.stringify(v))),
    onSuccess: () => {
      idem.done();
      qc.invalidateQueries({ queryKey: queryKeys.tickets.all });
    },
    onError: (e) => settleKey(idem, e),
  });
}

/** Make a ticket from a Cevanos chat. */
export function useTicketFromConversation() {
  const qc = useQueryClient();
  const [idem] = useState(createIdempotency);
  return useMutation({
    mutationFn: (conversationId: string) =>
      api.post<Ticket>(
        `tickets/from-conversation/${encodeURIComponent(conversationId)}`,
        undefined,
        idem.header(),
      ),
    onSuccess: () => {
      idem.done();
      qc.invalidateQueries({ queryKey: queryKeys.tickets.all });
    },
    onError: (e) => settleKey(idem, e),
  });
}

/** Take a handover ticket. Until accepted, replies are refused (409); notes still work. */
export function useAcceptTicket(id: string) {
  const qc = useQueryClient();
  const [idem] = useState(createIdempotency);
  return useMutation({
    mutationFn: () => api.post<Ticket>(`tickets/${id}/accept`, undefined, idem.header()),
    meta: { loud422: true },
    onSuccess: () => idem.done(),
    onError: (e) => settleKey(idem, e),
    onSettled: () => settle(qc, id),
  });
}

interface OptimisticContext {
  previous?: TicketWithEtag;
}

/**
 * Optimistically rewrites the cached ticket, returns what to roll back to.
 * The ETag is kept as-is: it still identifies the version the edit is based on.
 */
async function optimisticTicket(
  qc: QueryClient,
  id: string,
  change: (t: Ticket) => Ticket,
): Promise<OptimisticContext> {
  const key = queryKeys.tickets.detail(id);
  await qc.cancelQueries({ queryKey: key });
  const previous = qc.getQueryData<TicketWithEtag>(key);
  if (previous)
    qc.setQueryData<TicketWithEtag>(key, { ...previous, ticket: change(previous.ticket) });
  return { previous };
}

function rollback(qc: QueryClient, id: string, ctx: OptimisticContext | undefined) {
  if (ctx?.previous) qc.setQueryData(queryKeys.tickets.detail(id), ctx.previous);
}

function settle(qc: QueryClient, id: string) {
  qc.invalidateQueries({ queryKey: queryKeys.tickets.detail(id) });
  qc.invalidateQueries({ queryKey: queryKeys.tickets.lists });
}

/**
 * PATCH with `If-Match`. Applies the change to the cache immediately and rolls
 * back if the server refuses; a 409 then reloads the ticket
 */
export function useUpdateTicket(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ patch, etag }: { patch: TicketPatch; etag: string | null }) =>
      request<Ticket>(`tickets/${id}`, {
        method: "PATCH",
        body: patch,
        headers: etag ? { "If-Match": etag } : undefined,
      }),
    onMutate: ({ patch }) => optimisticTicket(qc, id, (t) => applyTicketPatch(t, patch)),
    onError: (_e, _v, ctx) => rollback(qc, id, ctx),
    onSettled: () => settle(qc, id),
  });
}

export function useResolveTicket(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ etag }: { etag: string | null }) =>
      request<Ticket>(`tickets/${id}/resolve`, {
        method: "POST",
        headers: etag ? { "If-Match": etag } : undefined,
      }),
    onMutate: () => optimisticTicket(qc, id, (t) => applyTicketPatch(t, { status: "resolved" })),
    onError: (_e, _v, ctx) => rollback(qc, id, ctx),
    onSettled: () => settle(qc, id),
  });
}

export function useSendMessage(id: string) {
  const qc = useQueryClient();
  const [idem] = useState(createIdempotency);
  return useMutation({
    mutationFn: (v: {
      kind: "reply" | "note";
      body: string;
      mention_ids?: string[];
      /** Upload each file first (useUploadAttachment), then list the ids here (max 10). */
      attachment_ids?: string[];
    }) => api.post(`tickets/${id}/messages`, v, idem.header(JSON.stringify(v))),
    meta: { loud422: true },
    onSuccess: () => {
      idem.done();
      qc.invalidateQueries({ queryKey: queryKeys.tickets.detail(id) });
      qc.invalidateQueries({ queryKey: queryKeys.tickets.attachments(id) });
    },
    onError: (e) => settleKey(idem, e),
  });
}

export function useAttachments(id: string) {
  return useQuery({
    queryKey: queryKeys.tickets.attachments(id),
    queryFn: () => api.get<unknown>(`tickets/${id}/attachments`),
    select: (d) => listOf<Attachment>(d),
    staleTime: 30_000,
  });
}

/** Raw file body, not multipart. Max 25 MB. */
export function useUploadAttachment(id: string) {
  const qc = useQueryClient();
  // One key per file, reused if the same File is sent again after a failure.
  const [keys] = useState(() => new WeakMap<File, string>());
  return useMutation({
    meta: { loud422: true },
    mutationFn: (file: File) => {
      let key = keys.get(file);
      if (!key) keys.set(file, (key = newId()));
      return request<Attachment>(`tickets/${id}/attachments`, {
        method: "POST",
        query: { name: file.name },
        body: file,
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "Idempotency-Key": key,
        },
      }).then((r) => r.data);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tickets.attachments(id) }),
  });
}

/**
 * Signed URL expires quickly: fetch it at click time and open straight away.
 * A 409 means the virus scan is still running ("Checking…"): the caller shows
 * `ApiError.userMessage` and lets the user try again.
 */
export async function downloadAttachment(id: string) {
  const { url } = await api.get<{ url: string; expires_at: string }>(`attachments/${id}`);
  // The URL comes from the API; never open anything but a plain web link
  // (blocks javascript:, data: and similar schemes if the response is tampered with).
  let target: URL;
  try {
    target = new URL(url);
  } catch {
    throw new Error("Invalid download link.");
  }
  if (target.protocol !== "https:" && target.protocol !== "http:") {
    throw new Error("Invalid download link.");
  }
  window.open(target.href, "_blank", "noopener,noreferrer");
}

export function useContactTickets(contactId: string) {
  return useQuery({
    queryKey: queryKeys.contacts.tickets(contactId),
    queryFn: async (): Promise<ContactTickets> => {
      const raw = await api.get<Record<string, unknown>>(`contacts/${contactId}/tickets`);
      return { ...(raw as object), items: listOf<TicketSummary>(raw) } as ContactTickets;
    },
    staleTime: 30_000,
  });
}

export function useUpdateContact(contactId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { name?: string; vip?: boolean }) =>
      api.patch(`contacts/${contactId}/profile`, v),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.contacts.tickets(contactId) });
      qc.invalidateQueries({ queryKey: queryKeys.tickets.all });
    },
  });
}

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ApiError, api, createIdempotency, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type {
  Article,
  ArticleCategory,
  PortalSession,
  PortalTicket,
  PortalTicketSummary,
  PublicCsat,
  PublicForm,
  PublicHelp,
  PublicType,
} from "@/lib/api/types";
import { newId } from "@/lib/new-id";

/**
 * Customer-facing data hooks. Portal calls carry the customer's own cookie (set by the
 * proxy at sign-in); nothing here ever touches the agent session. A 401 is handled
 * by the page (it shows the code sign-in), never by the agent login redirect.
 */

/** The workspace slug from the address. The proxy only lets plain path segments through. */
export function useWorkspaceSlug(): string {
  const p = useParams<{ workspace?: string }>();
  return p.workspace ?? "";
}

export const seg = encodeURIComponent;

/** A ticket type from the server, with `fields` always a list (older servers sent none). */
const withFields = (t: PublicType): PublicType => ({
  ...t,
  fields: Array.isArray(t.fields) ? t.fields : [],
});

export const isUnauthorized = (e: unknown) => e instanceof ApiError && e.status === 401;

/** 4xx means the server understood and refused: the same Idempotency-Key must not be reused. */
const clientError = (e: unknown) => e instanceof ApiError && e.status >= 400 && e.status < 500;

/* ---------- CSAT by emailed link ---------- */

export function usePublicCsat(token: string | null) {
  return useQuery({
    queryKey: queryKeys.public.csat(token ?? ""),
    queryFn: () => api.get<PublicCsat>(`public/csat/${seg(token!)}`),
    enabled: !!token,
    retry: false,
    staleTime: Infinity,
  });
}

export function useSubmitPublicCsat(token: string) {
  return useMutation({
    mutationFn: (v: { score: number; comment?: string }) =>
      api.post<unknown>(`public/csat/${seg(token)}`, v),
    meta: { silent: true },
  });
}

/* ---------- Portal sign-in (email -> 6-digit code) ---------- */

export function useRequestCode(ws: string) {
  return useMutation({
    mutationFn: (email: string) => api.post<unknown>(`portal/${seg(ws)}/code`, { email }),
    meta: { silent: true },
  });
}

export function useCodeSignIn(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { email: string; code: string }) =>
      api.post<PortalSession>(`portal/${seg(ws)}/session`, v),
    meta: { silent: true },
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.portal.all }),
  });
}

export function usePortalSignOut() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<void>("portal/sign-out"),
    meta: { silent: true },
    onSettled: () => qc.removeQueries({ queryKey: queryKeys.portal.all }),
  });
}

/* ---------- Portal tickets ---------- */

export function usePortalTickets(ws: string) {
  return useQuery({
    queryKey: queryKeys.portal.tickets(ws),
    queryFn: async () => listOf<PortalTicketSummary>(await api.get<unknown>("portal/tickets")),
    retry: false,
    staleTime: 15_000,
  });
}

export function usePortalTicketTypes(ws: string) {
  return useQuery({
    queryKey: queryKeys.portal.types(ws),
    queryFn: async () =>
      listOf<PublicType>(await api.get<unknown>("portal/ticket-types")).map(withFields),
    retry: false,
    staleTime: 60_000,
  });
}

export function useCreatePortalTicket(ws: string) {
  const qc = useQueryClient();
  const [idem] = useState(createIdempotency);
  return useMutation({
    // The server writes the subject from the first 60 characters of `message`.
    mutationFn: (v: {
      message: string;
      type_id?: string;
      fields?: Record<string, string | number>;
    }) => api.post<{ id: string }>("portal/tickets", v, idem.header(JSON.stringify(v))),
    meta: { silent: true },
    onSuccess: () => {
      idem.done();
      qc.invalidateQueries({ queryKey: queryKeys.portal.tickets(ws) });
    },
    onError: (e) => {
      if (clientError(e)) idem.done();
    },
  });
}

export function usePortalTicket(id: string) {
  return useQuery({
    queryKey: queryKeys.portal.ticket(id),
    queryFn: async () => {
      const raw = await api.get<PortalTicket | { ticket: PortalTicket }>(
        `portal/tickets/${seg(id)}`,
      );
      return ("ticket" in raw ? raw.ticket : raw) as PortalTicket;
    },
    retry: false,
    staleTime: 10_000,
  });
}

export function useSendPortalMessage(id: string) {
  const qc = useQueryClient();
  const [idem] = useState(createIdempotency);
  return useMutation({
    mutationFn: (v: { body: string }) =>
      api.post<unknown>(`portal/tickets/${seg(id)}/messages`, v, idem.header(JSON.stringify(v))),
    meta: { silent: true },
    onSuccess: () => {
      idem.done();
      qc.invalidateQueries({ queryKey: queryKeys.portal.ticket(id) });
    },
    onError: (e) => {
      if (clientError(e)) idem.done();
    },
  });
}

export function useRatePortalTicket(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { score: number; comment?: string }) =>
      api.post<unknown>(`portal/tickets/${seg(id)}/csat`, v),
    meta: { silent: true },
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.portal.ticket(id) }),
  });
}

/* ---------- Web form ---------- */

export function usePublicForm(ws: string) {
  return useQuery({
    queryKey: queryKeys.public.form(ws),
    queryFn: async (): Promise<PublicForm> => {
      const raw = await api.get<Record<string, unknown>>(`public/${seg(ws)}/form`);
      const types = listOf<PublicType>(raw?.types ?? raw?.ticket_types).map(withFields);
      return { ...(raw as object), types } as PublicForm;
    },
    retry: false,
    staleTime: 60_000,
  });
}

export function useSubmitPublicForm(ws: string) {
  const [idem] = useState(createIdempotency);
  return useMutation({
    mutationFn: (v: {
      name: string;
      email: string;
      message: string;
      type_id?: string;
      fields?: Record<string, string | number>;
      /** Spam trap: a real person never fills it, so it is always empty. */
      website: "";
    }) => api.post<unknown>(`public/${seg(ws)}/form`, v, idem.header(JSON.stringify(v))),
    meta: { silent: true },
    onSuccess: () => idem.done(),
    onError: (e) => {
      if (clientError(e)) idem.done();
    },
  });
}

/* ---------- Help center ---------- */

export function usePublicHelp(ws: string, q: string) {
  return useQuery({
    queryKey: queryKeys.public.help(ws, q),
    queryFn: async (): Promise<PublicHelp> => {
      const raw = await api.get<Record<string, unknown>>(`public/${seg(ws)}/articles`, { q });
      const articles = listOf<Article>(raw?.articles ?? raw);
      let categories = listOf<ArticleCategory>(raw?.categories);
      if (categories.length === 0) {
        // No categories sent: build them from what the articles say.
        const seen = new Map<string, ArticleCategory>();
        for (const a of articles) {
          if (a.category_id && !seen.has(a.category_id))
            seen.set(a.category_id, { id: a.category_id, name: a.category_name ?? "General" });
        }
        categories = [...seen.values()];
      }
      return {
        workspace_name: (raw?.workspace_name as string | undefined) ?? null,
        categories,
        articles,
      };
    },
    retry: false,
    placeholderData: (prev) => prev,
    staleTime: 60_000,
  });
}

export function usePublicArticle(ws: string, id: string) {
  return useQuery({
    queryKey: queryKeys.public.article(ws, id),
    queryFn: async () => {
      const raw = await api.get<Record<string, unknown>>(`public/${seg(ws)}/articles/${seg(id)}`);
      const article = (raw?.article ?? raw) as Article;
      return { article, related: listOf<Article>(raw?.related) };
    },
    retry: false,
    staleTime: 60_000,
  });
}

const VISITOR_KEY = "cdesk.visitor";

/**
 * A random id for this browser (8 to 100 characters), kept so one visitor cannot vote twice.
 * Storage can be blocked; then a fresh id is made for the page and the vote still goes out.
 */
export function visitorId(): string {
  try {
    const kept = localStorage.getItem(VISITOR_KEY);
    if (kept && kept.length >= 8 && kept.length <= 100) return kept;
    const made = newId();
    localStorage.setItem(VISITOR_KEY, made);
    return made;
  } catch {
    return newId();
  }
}

export function useVoteArticle(ws: string, id: string) {
  return useMutation({
    mutationFn: (helpful: boolean) =>
      api.post<unknown>(`public/${seg(ws)}/articles/${seg(id)}/vote`, {
        helpful,
        visitor: visitorId(),
      }),
    meta: { silent: true },
  });
}

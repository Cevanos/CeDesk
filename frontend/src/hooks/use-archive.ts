"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";
import type { Message } from "@/lib/api/types";

export const archiveKeys = {
  all: ["archive"] as const,
  list: (f: ArchiveSearch) => ["archive", "list", f] as const,
  one: (id: string) => ["archive", "one", id] as const,
};

/** What the person typed. A number (`1042`, `T-1042`), an email or a contact id. */
export interface ArchiveSearch {
  number?: string;
  email?: string;
  contact_id?: string;
}

export interface ArchivedTicketRow {
  id: string;
  label?: string | null;
  number?: number | string | null;
  subject?: string | null;
  status?: string | null;
  customer_name?: string | null;
  customer_email?: string | null;
  contact_name?: string | null;
  contact_email?: string | null;
  resolved_at?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
}

export interface ArchivedTicket extends ArchivedTicketRow {
  description?: string | null;
  priority?: string | null;
  channel?: string | null;
  messages?: Message[];
  attachments?: { id?: string; name: string; size?: number }[];
  csat?: { score: number; comment?: string | null; rated_at?: string | null } | null;
  rating?: { score: number; comment?: string | null } | number | null;
  /** Names as they were when the ticket was archived. */
  team_name?: string | null;
  assignee_name?: string | null;
  tags?: string[];
  fields?: Record<string, string | number | null>;
  files?: { id?: string; name: string; size?: number; mime?: string }[];
}

/** Contact's archived tickets, shown on the customer page. */
export function useContactArchive(contactId: string) {
  return useQuery({
    queryKey: archiveKeys.list({ contact_id: contactId }),
    queryFn: async () =>
      listOf<ArchivedTicketRow>(
        await api.get<unknown>("archive/tickets", { contact_id: contactId }),
      ),
    retry: false,
    staleTime: 60_000,
  });
}

/** Turns one search box into the query the API wants. Returns null for an empty box. */
export function parseArchiveQuery(input: string): ArchiveSearch | null {
  const q = input.trim();
  if (!q) return null;
  if (q.includes("@")) return { email: q };
  const num = /^t?-?(\d{1,10})$/i.exec(q);
  if (num) return { number: num[1] };
  // Anything else is taken as a customer id.
  return { contact_id: q };
}

interface Page {
  items: ArchivedTicketRow[];
  next_cursor: string | null;
}

export function useArchiveSearch(filters: ArchiveSearch | null) {
  return useInfiniteQuery({
    queryKey: archiveKeys.list(filters ?? {}),
    enabled: !!filters,
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }): Promise<Page> => {
      const d = await api.get<unknown>("archive/tickets", { ...filters, cursor: pageParam });
      const next = (d as { next_cursor?: string | null } | null)?.next_cursor ?? null;
      return { items: listOf<ArchivedTicketRow>(d), next_cursor: next };
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    retry: false,
  });
}

export function useArchivedTicket(id: string) {
  return useQuery({
    queryKey: archiveKeys.one(id),
    queryFn: () => api.get<ArchivedTicket>(`archive/tickets/${encodeURIComponent(id)}`),
    retry: false,
  });
}

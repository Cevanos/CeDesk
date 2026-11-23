"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";
import type { Contact } from "@/lib/api/types";

/** One row of GET /privacy/requests. */
export interface PrivacyRequest {
  id: string;
  /** "export" or "erase". */
  kind: string;
  contact_id: string;
  requested_by_name?: string | null;
  requested_at: string;
  customer_erased?: boolean;
  /** An export can be downloaded while this is true (7 days, and not after an erase). */
  file_ready?: boolean;
  size?: number | null;
  expires_at?: string | null;
  counts?: Record<string, number> | null;
}

const requestsKey = ["privacy", "requests"] as const;

export function usePrivacyRequests() {
  return useQuery({
    queryKey: requestsKey,
    queryFn: ({ signal }) => api.get<unknown>("privacy/requests", undefined, signal),
    select: listOf<PrivacyRequest>,
  });
}

export function useExportCustomer() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (email: string) => api.post<PrivacyRequest>("privacy/export", { email }),
    onSuccess: () => qc.invalidateQueries({ queryKey: requestsKey }),
  });
}

/** Backend needs `contact_id` (not an email) plus the email typed again. */
export function useEraseCustomer() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { contact_id: string; confirm_email: string }) =>
      api.post<PrivacyRequest>("privacy/erase", v),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: requestsKey });
      qc.invalidateQueries({ queryKey: ["tickets"] });
    },
  });
}

/** The customer behind a ticket row: `contact.id` is what erase needs. */
export interface FoundCustomer {
  id: string;
  name: string;
  email: string;
}

/**
 * Find a customer by email through the ticket search (`GET /tickets?q=`), taking the contact
 * from the rows. Null when nobody with that exact email has a ticket.
 */
export function useFindCustomer() {
  return useMutation({
    meta: { silent: true },
    mutationFn: async (email: string): Promise<FoundCustomer | null> => {
      const wanted = email.trim().toLowerCase();
      const raw = await api.get<unknown>("tickets", { q: wanted, limit: 50 });
      for (const row of listOf<{ contact?: Partial<Contact> }>(raw)) {
        const c = row.contact;
        if (c?.id && c.email && c.email.toLowerCase() === wanted) {
          return { id: c.id, name: c.name ?? "", email: c.email };
        }
      }
      return null;
    },
  });
}

export function privacyFilePath(id: string) {
  return `privacy/requests/${encodeURIComponent(id)}/file`;
}

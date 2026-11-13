"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";

export interface PrivacyRequest {
  id: string;
  kind?: string;
  type?: string;
  email?: string;
  customer_email?: string;
  status?: string;
  created_at?: string;
  expires_at?: string | null;
  [key: string]: unknown;
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
    mutationFn: (email: string) => api.post<PrivacyRequest>("privacy/export", { email }),
    onSuccess: () => qc.invalidateQueries({ queryKey: requestsKey }),
  });
}

export function useEraseCustomer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (email: string) =>
      api.post<PrivacyRequest>("privacy/erase", { email, confirm_email: email }),
    onSuccess: () => qc.invalidateQueries({ queryKey: requestsKey }),
  });
}

export function privacyFileUrl(id: string) {
  return `/api/privacy/requests/${encodeURIComponent(id)}/file`;
}

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";

export interface ApiKeyRow {
  id: string;
  name?: string;
  prefix?: string;
  last4?: string;
  created_at?: string;
  last_used_at?: string | null;
}

/** Creation answer: the full key is only here, once. */
export interface ApiKeyCreated extends ApiKeyRow {
  key?: string;
  token?: string;
  secret?: string;
}

export const apiKeysKey = ["api-keys"] as const;

export function useApiKeys() {
  return useQuery({
    queryKey: apiKeysKey,
    queryFn: () => api.get<unknown>("api-keys"),
    select: listOf<ApiKeyRow>,
  });
}

export function useCreateApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string }) => api.post<ApiKeyCreated>("api-keys", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: apiKeysKey }),
  });
}

export function useRevokeApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`api-keys/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: apiKeysKey }),
  });
}

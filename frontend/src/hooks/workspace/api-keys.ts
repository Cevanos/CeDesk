"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";

export type ApiKeyAccess = "read" | "read_write" | "admin";

export interface ApiKeyRow {
  id: string;
  name?: string;
  prefix?: string;
  last4?: string;
  access?: ApiKeyAccess;
  hint?: string;
  created_by_name?: string | null;
  revoked_at?: string | null;
  revoked_by_name?: string | null;
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
    mutationFn: (body: { name: string; access: ApiKeyAccess }) =>
      api.post<ApiKeyCreated>("api-keys", body),
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

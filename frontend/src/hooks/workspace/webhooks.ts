"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";

export interface WebhookRow {
  id: string;
  url: string;
  events?: string[];
  active?: boolean;
  enabled?: boolean;
  description?: string;
  secret_hint?: string | null;
  created_at?: string;
}

export interface WebhookCreated extends WebhookRow {
  secret?: string;
}

export interface WebhookDelivery {
  id: string;
  webhook_id?: string;
  event?: string;
  status?: string;
  response_status?: number | null;
  attempts?: number;
  error?: string | null;
  created_at?: string;
}

export const webhookKeys = {
  all: ["webhooks"] as const,
  deliveries: ["webhooks", "deliveries"] as const,
};

export function useWebhooks() {
  return useQuery({
    queryKey: webhookKeys.all,
    queryFn: () => api.get<unknown>("webhooks"),
    select: listOf<WebhookRow>,
  });
}

export function useWebhookDeliveries(enabled = true) {
  return useQuery({
    queryKey: webhookKeys.deliveries,
    queryFn: () => api.get<unknown>("webhooks/deliveries"),
    select: listOf<WebhookDelivery>,
    enabled,
  });
}

export interface WebhookInput {
  url: string;
  events: string[];
  active?: boolean;
}

export function useSaveWebhook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: Partial<WebhookInput> & { id?: string }) =>
      id
        ? api.patch<WebhookRow>(`webhooks/${id}`, body).then((r) => r.data as WebhookCreated)
        : api.post<WebhookCreated>("webhooks", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: webhookKeys.all }),
  });
}

export function useDeleteWebhook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`webhooks/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: webhookKeys.all }),
  });
}

export function useRotateWebhookSecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<{ secret?: string }>(`webhooks/${id}/secret`),
    onSuccess: () => qc.invalidateQueries({ queryKey: webhookKeys.all }),
  });
}

export function useTestWebhook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<unknown>(`webhooks/${id}/test`),
    onSuccess: () => qc.invalidateQueries({ queryKey: webhookKeys.deliveries }),
  });
}

export function useRetryDelivery() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<unknown>(`webhooks/deliveries/${id}/retry`),
    onSuccess: () => qc.invalidateQueries({ queryKey: webhookKeys.deliveries }),
  });
}

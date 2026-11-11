"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { NotificationList } from "@/lib/api/types";

/* ---- notifications: no push in Phase 1, poll every 30 s (guide §5.2) ---- */
export function useNotifications() {
  return useQuery({
    queryKey: queryKeys.notifications.list(),
    queryFn: async (): Promise<NotificationList> => {
      const raw = await api.get<Record<string, unknown>>("notifications");
      return { items: listOf(raw), unread: Number(raw?.unread ?? 0) } as NotificationList;
    },
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });
}

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<void>(`notifications/${id}/read`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.notifications.all }),
  });
}

export function useMarkAllRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<void>("notifications/read-all"),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.notifications.all }),
  });
}

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

export function useRoutingDefaults() {
  return useQuery({
    queryKey: queryKeys.routingDefaults.all,
    queryFn: () =>
      api.get<{ cap_email: number; cap_chat: number; cap_messaging: number }>("routing-defaults"),
  });
}

export function useUpdateRoutingDefaults() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: Partial<{ cap_email: number; cap_chat: number; cap_messaging: number }>) =>
      api.patch("routing-defaults", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.routingDefaults.all }),
  });
}

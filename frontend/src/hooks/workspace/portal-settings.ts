"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { PortalSettings } from "@/lib/api/types";

export function usePortalSettings() {
  return useQuery({
    queryKey: queryKeys.portalSettings.all,
    queryFn: async (): Promise<PortalSettings> => {
      const raw = await api.get<Partial<PortalSettings>>("portal-settings");
      return {
        allowed_email_domains: raw?.allowed_email_domains ?? [],
        csat_enabled: raw?.csat_enabled === true,
        web_form_enabled: raw?.web_form_enabled === true,
      };
    },
    staleTime: 60_000,
  });
}

export function useSavePortalSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PortalSettings) => api.put<PortalSettings>("portal-settings", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.portalSettings.all }),
  });
}

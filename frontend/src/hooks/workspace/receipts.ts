"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { withOffset, type ReportParams } from "@/hooks/workspace/reports";
import { api } from "@/lib/api/client";
import type {
  ReceiptSettings,
  Resolution,
  ResolutionInput,
  ResolutionsReport,
} from "@/lib/api/differentiator-types";
import { queryKeys } from "@/lib/api/query-keys";

/** Loaded once with the app: the resolve form depends on `mode` and the ticket's team. */
export function useReceiptSettings() {
  return useQuery({
    queryKey: queryKeys.receipts.settings,
    queryFn: async (): Promise<ReceiptSettings> => {
      const raw = await api.get<Partial<ReceiptSettings> | null>("resolution-receipts");
      return {
        mode: raw?.mode === "optional" || raw?.mode === "required" ? raw.mode : "off",
        required_teams: raw?.required_teams ?? [],
        updated_by_name: raw?.updated_by_name ?? null,
        updated_at: raw?.updated_at ?? null,
      };
    },
    staleTime: 5 * 60_000,
  });
}

export function useSaveReceiptSettings() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (body: Pick<ReceiptSettings, "mode" | "required_teams">) =>
      api.put<ReceiptSettings>("resolution-receipts", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.receipts.settings }),
  });
}

/** Is a receipt needed for a ticket in this team? */
export function receiptRequired(s: ReceiptSettings | undefined, teamId?: string | null): boolean {
  if (!s || s.mode !== "required") return false;
  return s.required_teams.length === 0 || (!!teamId && s.required_teams.includes(teamId));
}

function refresh(qc: ReturnType<typeof useQueryClient>, ticketId: string) {
  qc.invalidateQueries({ queryKey: queryKeys.tickets.detail(ticketId) });
  qc.invalidateQueries({ queryKey: queryKeys.tickets.lists });
}

/** Edit a receipt: only the changed fields. 409 after 7 days or when not resolved. */
export function useEditResolution(ticketId: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (patch: Partial<ResolutionInput>) =>
      api.patch<Resolution>(`tickets/${ticketId}/resolution`, patch).then((r) => r.data),
    onSuccess: () => refresh(qc, ticketId),
  });
}

/** Check against Shopify or Stripe (read only there). */
export function useVerifyResolution(ticketId: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { order_id: string } | { payment_id: string }) =>
      api.post<Resolution>(`tickets/${ticketId}/resolution/verify`, v),
    onSuccess: () => refresh(qc, ticketId),
  });
}

export function resolutionsQuery(params: ReportParams) {
  return {
    from: withOffset(params.from),
    to: withOffset(params.to, true),
    team_id: params.team_id,
  };
}

export function useResolutionsReport(params: ReportParams) {
  return useQuery({
    queryKey: queryKeys.receipts.report(params),
    queryFn: async (): Promise<ResolutionsReport> => {
      const raw = await api.get<Partial<ResolutionsReport> | null>(
        "reports/resolutions",
        resolutionsQuery(params),
      );
      return {
        total: {
          resolved: 0,
          with_receipt: 0,
          verified: 0,
          verified_agent: 0,
          verified_shopify: 0,
          verified_stripe: 0,
          pending: 0,
          notified: 0,
          ...(raw?.total ?? {}),
        },
        teams: raw?.teams ?? [],
        agents: raw?.agents ?? [],
      };
    },
  });
}

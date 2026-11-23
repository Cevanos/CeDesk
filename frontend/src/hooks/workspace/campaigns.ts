"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";

import { api, downloadFile } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type {
  Campaign,
  CampaignAudience,
  CampaignPreview,
  CampaignRecipient,
  CampaignSettings,
  CampaignStats,
  PublicUnsubscribe,
  SuppressionEntry,
  SuppressionReason,
} from "@/lib/api/types";

const PAGE = 50;
const seg = encodeURIComponent;

/** A sending campaign changes on its own, so its pages refresh themselves. */
const isLive = (c: Pick<Campaign, "state"> | undefined) => c?.state === "sending";

/* ---------- settings ---------- */

export function useCampaignSettings(enabled = true) {
  return useQuery({
    queryKey: queryKeys.campaigns.settings,
    queryFn: () => api.get<CampaignSettings>("campaigns/settings"),
    enabled,
    staleTime: 60_000,
  });
}

export function useSaveCampaignSettings() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (postal_address: string) =>
      api.put<CampaignSettings>("campaigns/settings", { postal_address }),
    onSuccess: (d) => {
      qc.setQueryData(queryKeys.campaigns.settings, d);
      // The "Before you can send" lists depend on the address.
      qc.invalidateQueries({ queryKey: queryKeys.campaigns.all });
    },
  });
}

/* ---------- list ---------- */

interface CampaignPage {
  campaigns: Campaign[];
  next_cursor: string | null;
}

export function useCampaigns(state: string, enabled = true) {
  return useInfiniteQuery({
    queryKey: queryKeys.campaigns.list(state),
    enabled,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      api.get<CampaignPage>(
        "campaigns",
        { state: state === "all" ? undefined : state, limit: PAGE, cursor: pageParam },
        signal,
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: (q) =>
      (q.state.data as InfiniteData<CampaignPage> | undefined)?.pages.some((p) =>
        p.campaigns.some(isLive),
      )
        ? 20_000
        : false,
  });
}

export function useCreateCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.post<Campaign>("campaigns", { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.campaigns.all }),
  });
}

/* ---------- one campaign ---------- */

export function useCampaign(id: string) {
  return useQuery({
    queryKey: queryKeys.campaigns.detail(id),
    queryFn: ({ signal }) => api.get<Campaign>(`campaigns/${seg(id)}`, undefined, signal),
    retry: false,
    refetchInterval: (q) => (isLive(q.state.data) ? 15_000 : false),
  });
}

export interface CampaignPatch {
  name?: string;
  segment_id?: string | null;
  subject?: string;
  body?: string;
  from_address_id?: string | null;
  track_opens?: boolean;
}

export function useUpdateCampaign(id: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (patch: CampaignPatch) =>
      api.patch<Campaign>(`campaigns/${seg(id)}`, patch).then((r) => r.data),
    onSuccess: (d, patch) => {
      qc.setQueryData(queryKeys.campaigns.detail(id), d);
      qc.invalidateQueries({ queryKey: ["campaigns", "list"] });
      if ("segment_id" in patch) {
        qc.invalidateQueries({ queryKey: queryKeys.campaigns.audience(id) });
      }
    },
  });
}

export function useDeleteCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`campaigns/${seg(id)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.campaigns.all }),
  });
}

export function useCampaignAudience(id: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.campaigns.audience(id),
    queryFn: ({ signal }) =>
      api.get<CampaignAudience>(`campaigns/${seg(id)}/audience`, undefined, signal),
    enabled,
    retry: false,
    // The send dialog must never show a count from before the Admin changed something.
    staleTime: 0,
  });
}

export function useCampaignPreview(id: string) {
  return useMutation({
    meta: { silent: true },
    mutationFn: (contactId?: string) =>
      api.post<CampaignPreview>(
        `campaigns/${seg(id)}/preview`,
        contactId ? { contact_id: contactId } : {},
      ),
  });
}

export function useSendCampaignTest(id: string) {
  return useMutation({
    // 422 / 429 carry a message written for the Admin: show it as a toast.
    meta: { loud422: true },
    mutationFn: () => api.post<{ sent_to: string }>(`campaigns/${seg(id)}/test`),
  });
}

/* ---------- send / pause / resume / cancel ---------- */

export function useSendCampaign(id: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { scheduled_at?: string }) =>
      api.post<Campaign>(`campaigns/${seg(id)}/send`, { confirm: true, ...v }),
    onSuccess: (d) => {
      qc.setQueryData(queryKeys.campaigns.detail(id), d);
      qc.invalidateQueries({ queryKey: queryKeys.campaigns.all });
    },
  });
}

/** `pause`, `resume` and `cancel` all answer with the updated campaign. */
export function useCampaignAction(id: string, action: "pause" | "resume" | "cancel") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<Campaign>(`campaigns/${seg(id)}/${action}`, {}),
    onSuccess: (d) => {
      qc.setQueryData(queryKeys.campaigns.detail(id), d);
      qc.invalidateQueries({ queryKey: queryKeys.campaigns.all });
    },
  });
}

/* ---------- results ---------- */

export function useCampaignStats(id: string, live: boolean) {
  return useQuery({
    queryKey: queryKeys.campaigns.stats(id),
    queryFn: ({ signal }) =>
      api.get<CampaignStats>(`campaigns/${seg(id)}/stats`, undefined, signal),
    refetchInterval: live ? 15_000 : false,
    retry: false,
  });
}

interface RecipientPage {
  recipients: CampaignRecipient[];
  next_cursor: string | null;
}

export function useCampaignRecipients(id: string, status: string) {
  return useInfiniteQuery({
    queryKey: queryKeys.campaigns.recipients(id, status),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      api.get<RecipientPage>(
        `campaigns/${seg(id)}/recipients`,
        { status: status === "all" ? undefined : status, limit: PAGE, cursor: pageParam },
        signal,
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    retry: false,
  });
}

export function exportCampaignRecipients(id: string) {
  return downloadFile(`campaigns/${seg(id)}/recipients/export`, "campaign-recipients.csv");
}

/* ---------- suppression list ---------- */

interface SuppressionPage {
  entries: SuppressionEntry[];
  counts: Partial<Record<SuppressionReason, number>>;
  next_cursor: string | null;
}

export function useSuppressions(filters: { reason: string; email: string }) {
  return useInfiniteQuery({
    queryKey: queryKeys.suppressions.list(filters),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      api.get<SuppressionPage>(
        "suppressions",
        {
          reason: filters.reason === "all" ? undefined : filters.reason,
          email: filters.email || undefined,
          limit: PAGE,
          cursor: pageParam,
        },
        signal,
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    retry: false,
  });
}

export function useAddSuppression() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { email: string; note?: string }) =>
      api.post<SuppressionEntry>("suppressions", v),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.suppressions.all });
      // The recipient count of every draft changes with the list.
      qc.invalidateQueries({ queryKey: queryKeys.campaigns.all });
    },
  });
}

export function useRemoveSuppression() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: ({
      id,
      ...body
    }: {
      id: string;
      confirm_consent?: boolean;
      justification?: string;
    }) => api.post<void>(`suppressions/${seg(id)}/remove`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.suppressions.all });
      qc.invalidateQueries({ queryKey: queryKeys.campaigns.all });
    },
  });
}

/* ---------- public unsubscribe page (no login) ---------- */

export function usePublicUnsubscribe(token: string | null) {
  return useQuery({
    queryKey: queryKeys.public.unsubscribe(token ?? ""),
    queryFn: () => api.get<PublicUnsubscribe>(`public/unsubscribe/${seg(token!)}`),
    enabled: !!token,
    retry: false,
    staleTime: Infinity,
  });
}

/** Only ever called from the button: opening the page must not unsubscribe anyone. */
export function usePublicUnsubscribeSubmit(token: string) {
  return useMutation({
    meta: { silent: true },
    mutationFn: () =>
      api.post<{ unsubscribed: boolean; message: string }>(`public/unsubscribe/${seg(token)}`, {}),
  });
}

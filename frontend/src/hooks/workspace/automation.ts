"use client";

import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useMemo } from "react";

import { useCurrentUser } from "@/hooks/use-auth";
import type {
  AutoItem,
  AutomationCatalog,
  AutomationRun,
  Flow,
  FlowGraph,
  FlowTestResult,
  FlowValidation,
  FlowVersion,
  RuleTestResult,
  RunFilters,
} from "@/lib/api/automation-types";
import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import { hasAction } from "@/lib/permissions";

/**
 * What the signed-in person may do with automation. `settings` opens the pages; `manage_access`
 * (an Admin) is also needed for anything that calls a webhook. The API enforces both with 403.
 */
export function useAutomationAccess() {
  const { user, isPending } = useCurrentUser();
  return useMemo(
    () => ({
      isPending,
      settings: hasAction(user, "settings", user?.role === "admin"),
      manageAccess: hasAction(user, "manage_access", user?.role === "admin"),
    }),
    [user, isPending],
  );
}

/** True when a rule's actions or a workflow's nodes call a webhook. */
export function callsWebhook(actions: AutoItem[]): boolean {
  return actions.some((a) => a.kind === "call_webhook");
}
export function flowCallsWebhook(graph: FlowGraph): boolean {
  return callsWebhook(graph.nodes.flatMap((n) => (n.type === "action" ? [n.action] : [])));
}

/* ---------- catalog ---------- */

export function useAutomationCatalog() {
  return useQuery({
    queryKey: queryKeys.automation.catalog,
    queryFn: () => api.get<AutomationCatalog>("automation/catalog"),
    staleTime: 10 * 60_000,
  });
}

/* ---------- rule test mode ---------- */

export type RuleDraft = {
  name: string;
  event: string;
  conditions: AutoItem[];
  actions: AutoItem[];
};

/** A saved rule by id, or the draft in the editor. */
export function useTestRule() {
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { ticketId: string; ruleId?: string; draft?: RuleDraft }) =>
      v.ruleId
        ? api.post<RuleTestResult>(`rules/${v.ruleId}/test`, { ticket_id: v.ticketId })
        : api.post<RuleTestResult>("rules/test", { ticket_id: v.ticketId, rule: v.draft }),
  });
}

/* ---------- workflows ---------- */

export function useFlows() {
  return useQuery({
    queryKey: queryKeys.flows.all,
    queryFn: () => api.get<unknown>("flows"),
    select: listOf<Flow>,
    staleTime: 15_000,
  });
}

export function useFlow(id: string | null) {
  return useQuery({
    queryKey: queryKeys.flows.detail(id ?? ""),
    enabled: !!id,
    queryFn: ({ signal }) => api.get<Flow>(`flows/${id}`, undefined, signal),
  });
}

export function useFlowVersions(id: string | null) {
  return useQuery({
    queryKey: queryKeys.flows.versions(id ?? ""),
    enabled: !!id,
    queryFn: () => api.get<unknown>(`flows/${id}/versions`),
    select: listOf<FlowVersion>,
  });
}

export interface FlowInput {
  name: string;
  graph: FlowGraph;
  enabled?: boolean;
  max_runs_per_hour?: number;
  max_runs_per_day?: number;
}

/** Create (no id) or change (id; send only what changes). */
export function useSaveFlow() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: ({ id, ...body }: Partial<FlowInput> & { id?: string }) =>
      id ? api.patch<Flow>(`flows/${id}`, body).then((r) => r.data) : api.post<Flow>("flows", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.flows.all }),
  });
}

/** Turn on or off from the list. Errors toast (403 for a webhook workflow). */
export function useToggleFlow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.patch<Flow>(`flows/${id}`, { enabled }).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.flows.all }),
  });
}

export function useDeleteFlow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`flows/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.flows.all });
      qc.invalidateQueries({ queryKey: ["automation"] });
    },
  });
}

/** The live check while drawing: the shape only, debounced by the caller. */
export function useFlowValidation(graph: FlowGraph | null) {
  const key = graph ? JSON.stringify(graph) : "";
  return useQuery({
    queryKey: queryKeys.flows.validate(key),
    enabled: !!graph,
    queryFn: () => api.post<FlowValidation>("flows/validate", { graph }),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    retry: false,
  });
}

/** A saved workflow (its current version) by id, or the graph on the canvas. */
export function useTestFlow() {
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { ticketId: string; flowId?: string; graph?: FlowGraph }) =>
      v.flowId
        ? api.post<FlowTestResult>(`flows/${v.flowId}/test`, { ticket_id: v.ticketId })
        : api.post<FlowTestResult>("flows/test", { ticket_id: v.ticketId, graph: v.graph }),
  });
}

/* ---------- run log ---------- */

const PAGE = 50;

export function useAutomationRuns(filters: RunFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.automation.runs(filters),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      api.get<{ runs: AutomationRun[]; next_cursor: string | null }>("automation/runs", {
        ...filters,
        limit: PAGE,
        cursor: pageParam,
      }),
    getNextPageParam: (l) => l.next_cursor ?? undefined,
  });
}

export function useCancelRun() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (id: string) => api.post<AutomationRun>(`automation/runs/${id}/cancel`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["automation", "runs"] });
      qc.invalidateQueries({ queryKey: queryKeys.flows.all });
    },
  });
}

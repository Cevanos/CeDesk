"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { RuleDraft } from "@/hooks/workspace/automation";
import type { Flow } from "@/lib/api/automation-types";
import { api } from "@/lib/api/client";
import type { Replay, ReplayState } from "@/lib/api/differentiator-types";
import { queryKeys } from "@/lib/api/query-keys";

export const replayActive = (s: ReplayState | undefined) => s === "queued" || s === "running";

export type ReplayTarget =
  | { kind: "draft"; draft: RuleDraft }
  | { kind: "rule"; ruleId: string }
  | { kind: "flow"; flowId: string; version?: number };

/**
 * Start a replay (202). A 409 means another replay is already queued or running; the caller
 * shows the message, so the global toast is silenced.
 */
export function useStartReplay() {
  return useMutation({
    meta: { silent: true },
    mutationFn: ({ target, limit }: { target: ReplayTarget; limit: number }) => {
      switch (target.kind) {
        case "draft":
          return api.post<Replay>("rules/replay", { rule: target.draft, ticket_limit: limit });
        case "rule":
          return api.post<Replay>(`rules/${target.ruleId}/replay`, { ticket_limit: limit });
        case "flow":
          return api.post<Replay>(`flows/${target.flowId}/replay`, {
            ticket_limit: limit,
            ...(target.version ? { version: target.version } : {}),
          });
      }
    },
  });
}

/** Polls every 2.5 s while queued or running, and stops at done, failed or cancelled. */
export function useReplay(id: string) {
  return useQuery({
    queryKey: queryKeys.replays.detail(id),
    queryFn: ({ signal }) => api.get<Replay>(`replays/${id}`, undefined, signal),
    refetchInterval: (q) => (replayActive(q.state.data?.state) ? 2500 : false),
  });
}

export function useCancelReplay(id: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: () => api.post<Replay>(`replays/${id}/cancel`),
    onSettled: () => qc.invalidateQueries({ queryKey: queryKeys.replays.detail(id) }),
  });
}

/** The old graph is saved as a new version; history is never rewritten. 404 and 409 show their message. */
export function useRestoreFlowVersion(flowId: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (version: number) => api.post<Flow>(`flows/${flowId}/versions/${version}/restore`),
    onSuccess: (flow) => {
      qc.setQueryData(queryKeys.flows.detail(flowId), flow);
      qc.invalidateQueries({ queryKey: queryKeys.flows.all });
      qc.invalidateQueries({ queryKey: queryKeys.flows.versions(flowId) });
    },
  });
}

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { AgentRow, AgentStatus, ChannelGroup } from "@/lib/api/types";

export function useAgents({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    enabled,
    queryKey: queryKeys.agents.all,
    queryFn: () => api.get<unknown>("agents"),
    // The id may arrive as `member_id` or `id`; the UI always reads `member_id`.
    select: (d) =>
      listOf<AgentRow & { id?: string }>(d).map((a) => ({
        ...a,
        member_id: a.member_id ?? a.id ?? "",
        channels: a.channels ?? [],
        skills: a.skills ?? [],
        open_tickets: a.open_tickets ?? 0,
      })) as AgentRow[],
    refetchInterval: 30_000,
  });
}

export function useUpdateAgentRouting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      ...body
    }: {
      id: string;
      status?: AgentStatus;
      channels?: ChannelGroup[];
      capacity?: Record<string, number>;
      skills?: string[];
    }) => api.patch(`agents/${id}/routing`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.agents.all }),
  });
}

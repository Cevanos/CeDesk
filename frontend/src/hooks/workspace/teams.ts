"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { Team } from "@/lib/api/types";

/* ---- teams ---- */
export function useTeams() {
  return useQuery({
    queryKey: queryKeys.teams.all,
    queryFn: () => api.get<unknown>("teams"),
    select: listOf<Team>,
    staleTime: 60_000,
  });
}

export type TeamInput = Pick<Team, "name" | "auto_assign" | "max_open_per_agent" | "member_ids">;

export function useSaveTeam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: Partial<TeamInput> & { id?: string }) =>
      id ? api.patch(`teams/${id}`, body).then((r) => r.data) : api.post("teams", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.teams.all }),
  });
}

export function useDeleteTeam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`teams/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.teams.all }),
  });
}

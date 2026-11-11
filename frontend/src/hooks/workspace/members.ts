"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { Member, MemberList, Role } from "@/lib/api/types";

export function useMembers() {
  return useInfiniteQuery({
    queryKey: queryKeys.members.list,
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }): Promise<MemberList> => {
      const raw = await api.get<Record<string, unknown>>("members", {
        after: pageParam,
        limit: 50,
      });
      return { items: listOf<Member>(raw), next_after: (raw?.next_after as string | null) ?? null };
    },
    getNextPageParam: (l) => l.next_after ?? undefined,
  });
}

/** Everyone, for assignee pickers and @mentions. Light-weight: first 200. */
export function useMemberOptions(enabled = true) {
  return useQuery({
    queryKey: queryKeys.members.options,
    enabled,
    staleTime: 60_000,
    queryFn: () => api.get<unknown>("members", { limit: 200 }),
    select: (d) => listOf<Member>(d),
  });
}

export function useAddMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { email: string; name: string; role: Role; password: string }) =>
      api.post<Member>("members", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.members.all }),
  });
}

export function useUpdateMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      ...body
    }: {
      id: string;
      name?: string;
      role?: Role;
      is_active?: boolean;
    }) => api.patch(`members/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.members.all }),
  });
}

export function useResetMemberPassword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, new_password }: { id: string; new_password: string }) =>
      api.post<void>(`members/${id}/password`, { new_password }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.members.all }),
  });
}

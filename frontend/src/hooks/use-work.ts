"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";

export const workKeys = {
  approvals: ["work", "approvals"] as const,
  tasks: ["work", "tasks"] as const,
  all: ["work"] as const,
};

/** One request waiting for my decision (GET /approvals/waiting). Fields are inferred. */
export interface WaitingApproval {
  id: string;
  ticket_id?: string | null;
  ticket_label?: string | null;
  ticket_subject?: string | null;
  subject?: string | null;
  title?: string | null;
  reason?: string | null;
  note?: string | null;
  requested_by_name?: string | null;
  requester_name?: string | null;
  created_at?: string | null;
}

/** One of my open tasks (GET /tasks/mine). */
export interface MyTask {
  id: string;
  ticket_id?: string | null;
  ticket_label?: string | null;
  title?: string | null;
  text?: string | null;
  due_at?: string | null;
  done?: boolean;
}

const POLL = 60_000;

export function useWaitingApprovals(enabled = true) {
  return useQuery({
    queryKey: workKeys.approvals,
    enabled,
    queryFn: async () =>
      listOf<WaitingApproval>(await api.get<unknown>("approvals/waiting")).filter((a) => a?.id),
    refetchInterval: POLL,
    retry: false,
  });
}

export function useMyTasks() {
  return useQuery({
    queryKey: workKeys.tasks,
    queryFn: async () => listOf<MyTask>(await api.get<unknown>("tasks/mine")).filter((t) => t?.id),
    refetchInterval: POLL,
    retry: false,
  });
}

export function useDecideApproval() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: string; decision: "approve" | "reject"; comment?: string }) =>
      api.post<void>(`approvals/${encodeURIComponent(v.id)}/decide`, {
        decision: v.decision,
        ...(v.comment ? { comment: v.comment } : {}),
      }),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: workKeys.approvals });
      // A ticket page may show the same request.
      void qc.invalidateQueries({ queryKey: ["tickets"] });
    },
  });
}

export function useToggleTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: string; done: boolean }) =>
      api.patch<unknown>(`tasks/${encodeURIComponent(v.id)}`, { done: v.done }),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: workKeys.tasks });
      void qc.invalidateQueries({ queryKey: ["tickets"] });
    },
  });
}

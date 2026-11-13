"use client";

/**
 * Ticket page extras: related tickets, approvals, tasks, time, side conversations,
 * Jira, orders and payments. Types are deliberately loose (no OpenAPI spec yet) and
 * every card must degrade quietly: see `quietReason`.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, api, createIdempotency, listOf } from "@/lib/api/client";

export type ExtraKind =
  "related" | "approvals" | "tasks" | "time" | "side" | "jira" | "orders" | "payments";

export const extraKeys = {
  all: (ticketId: string) => ["ticket-extras", ticketId] as const,
  one: (ticketId: string, kind: ExtraKind) => ["ticket-extras", ticketId, kind] as const,
};

/* ---------- types (loose on purpose) ---------- */

export interface TicketRef {
  id: string;
  label?: string;
  subject?: string;
  status?: string;
  team_id?: string | null;
  team_name?: string | null;
}

export interface RelatedCard {
  parent: TicketRef | null;
  children: TicketRef[];
  links: TicketRef[];
  merges: TicketRef[];
}

export interface Approval {
  id: string;
  title?: string;
  note?: string;
  status?: string;
  approver_id?: string;
  approver_name?: string;
  decided_by_name?: string | null;
  decision_note?: string | null;
  created_at?: string;
}

export interface TicketTask {
  id: string;
  title: string;
  done?: boolean;
  due_at?: string | null;
  owner_id?: string | null;
  owner_name?: string | null;
}

export interface TimeEntry {
  id: string;
  minutes: number;
  note?: string | null;
  billable?: boolean;
  member_id?: string | null;
  member_name?: string | null;
  created_at?: string;
}
export interface TimeLog {
  entries: TimeEntry[];
  total_minutes: number;
  billable_minutes: number;
}

export interface SideMessage {
  id?: string;
  body?: string;
  from?: string | null;
  author_name?: string | null;
  direction?: string | null;
  created_at?: string;
}
export interface SideConversation {
  id: string;
  via: "email" | "slack" | (string & {});
  subject?: string | null;
  to?: string[] | string | null;
  channel?: string | null;
  status?: string | null;
  messages: SideMessage[];
  created_at?: string;
}

export interface JiraIssue {
  key: string;
  summary?: string | null;
  status?: string | null;
  url?: string | null;
}

/** Orders and payments share the same envelope. */
export interface CommerceCard {
  available: boolean;
  message?: string | null;
  items: Record<string, unknown>[];
}

/* ---------- helpers ---------- */

/** Message for an optional card that cannot load; null = a real, retryable error. */
export function quietReason(e: unknown): string | null {
  if (e instanceof ApiError && [402, 403, 404, 503].includes(e.status)) {
    if (e.status === 403) return "You don't have access to this.";
    if (e.status === 402) return "Not included in your plan.";
    if (e.status === 503) return e.message || "Not available right now.";
    return "Not available for this ticket.";
  }
  return null;
}

function useExtra<S>(
  ticketId: string,
  kind: ExtraKind,
  path: string,
  select: (d: unknown) => S,
  enabled = true,
) {
  return useQuery<unknown, Error, S>({
    queryKey: extraKeys.one(ticketId, kind),
    queryFn: () => api.get<unknown>(`tickets/${encodeURIComponent(ticketId)}/${path}`),
    select,
    enabled,
    retry: (n, e) => !(e instanceof ApiError && e.status >= 400 && e.status < 500) && n < 2,
    staleTime: 15_000,
  });
}

const obj = (d: unknown): Record<string, unknown> =>
  d && typeof d === "object" && !Array.isArray(d) ? (d as Record<string, unknown>) : {};

function ref(x: unknown): TicketRef | null {
  const o = obj(x);
  const id = o.id ?? o.ticket_id;
  return id ? ({ ...o, id: String(id) } as TicketRef) : null;
}
const refs = (x: unknown) =>
  (Array.isArray(x) ? x : []).map(ref).filter((r): r is TicketRef => !!r);

/** Mutation with a per-hook idempotency key that is reused only for an identical retry. */
function useExtraMutation<V, R = unknown>(
  ticketId: string,
  kinds: ExtraKind[],
  run: (v: V, headers: Record<string, string>) => Promise<R>,
  opts: { creating?: boolean; invalidateTicket?: boolean } = {},
) {
  const qc = useQueryClient();
  const [idem] = useState(createIdempotency);
  return useMutation({
    mutationFn: (v: V) => run(v, opts.creating ? idem.header(JSON.stringify(v)) : {}),
    onSuccess: () => {
      idem.done();
      for (const k of kinds) qc.invalidateQueries({ queryKey: extraKeys.one(ticketId, k) });
      if (opts.invalidateTicket) qc.invalidateQueries({ queryKey: ["tickets"] });
    },
    onError: (e) => {
      if (!(e instanceof ApiError) || [404, 409, 413, 422].includes(e.status)) idem.done();
    },
  });
}

const t = (id: string) => `tickets/${encodeURIComponent(id)}`;

/* ---------- related ---------- */

export function useRelated(ticketId: string) {
  return useExtra<RelatedCard>(ticketId, "related", "related", (d) => {
    const o = obj(d);
    return {
      parent: ref(o.parent),
      children: refs(o.children),
      links: refs(o.links),
      merges: refs(o.merges),
    };
  });
}

export function useMergeTicket(ticketId: string) {
  return useExtraMutation(
    ticketId,
    ["related"],
    (v: { into_id: string }, h) => api.post(`${t(ticketId)}/merge`, v, h),
    { creating: true, invalidateTicket: true },
  );
}
export function useLinkTicket(ticketId: string) {
  return useExtraMutation(
    ticketId,
    ["related"],
    (v: { other_id: string }, h) => api.post(`${t(ticketId)}/links`, v, h),
    { creating: true },
  );
}
export function useUnlinkTicket(ticketId: string) {
  return useExtraMutation(ticketId, ["related"], (otherId: string) =>
    api.delete(`${t(ticketId)}/links/${encodeURIComponent(otherId)}`),
  );
}
export function useCreateChild(ticketId: string) {
  return useExtraMutation(
    ticketId,
    ["related"],
    (v: { subject: string; team_id: string; description?: string }, h) =>
      api.post(`${t(ticketId)}/children`, v, h),
    { creating: true, invalidateTicket: true },
  );
}

/* ---------- approvals ---------- */

export function useApprovals(ticketId: string) {
  return useExtra<Approval[]>(ticketId, "approvals", "approvals", (d) =>
    listOf<Approval>(d).filter((a) => a && a.id),
  );
}
export function useAskApproval(ticketId: string) {
  return useExtraMutation(
    ticketId,
    ["approvals"],
    (v: { approver_id: string; title: string; note?: string }, h) =>
      api.post(`${t(ticketId)}/approvals`, v, h),
    { creating: true },
  );
}

/* ---------- tasks ---------- */

/** `1h`, `4h`, `tomorrow` are sent as typed; a date is sent as an ISO date/time. */
export type TaskDue = "1h" | "4h" | "tomorrow" | (string & {});

export function useTasks(ticketId: string) {
  return useExtra<TicketTask[]>(ticketId, "tasks", "tasks", (d) =>
    listOf<TicketTask>(d).filter((x) => x && x.id),
  );
}
export function useAddTask(ticketId: string) {
  return useExtraMutation(
    ticketId,
    ["tasks"],
    (v: { title: string; due?: TaskDue; owner_id?: string }, h) =>
      api.post(`${t(ticketId)}/tasks`, v, h),
    { creating: true },
  );
}
export function useUpdateTask(ticketId: string) {
  return useExtraMutation(
    ticketId,
    ["tasks"],
    (v: {
      id: string;
      title?: string;
      done?: boolean;
      due?: TaskDue | null;
      owner_id?: string | null;
    }) => {
      const { id, ...body } = v;
      return api.patch(`tasks/${encodeURIComponent(id)}`, body);
    },
  );
}

/* ---------- time ---------- */

export function useTimeLog(ticketId: string) {
  return useExtra<TimeLog>(ticketId, "time", "time", (d) => {
    const o = obj(d);
    const entries = listOf<TimeEntry>(o.entries ?? o.items ?? d).filter((e) => e && e.id);
    const sum = (f: (e: TimeEntry) => boolean) =>
      entries.reduce((n, e) => (f(e) ? n + (Number(e.minutes) || 0) : n), 0);
    return {
      entries,
      total_minutes: Number(o.total_minutes ?? o.total) || sum(() => true),
      billable_minutes: Number(o.billable_minutes ?? o.billable_total) || sum((e) => !!e.billable),
    };
  });
}
export function useLogTime(ticketId: string) {
  return useExtraMutation(
    ticketId,
    ["time"],
    (v: { minutes: number; note?: string; billable: boolean }, h) =>
      api.post(`${t(ticketId)}/time`, v, h),
    { creating: true },
  );
}
export function useDeleteTime(ticketId: string) {
  return useExtraMutation(ticketId, ["time"], (entryId: string) =>
    api.delete(`time/${encodeURIComponent(entryId)}`),
  );
}

/* ---------- side conversations ---------- */

export function useSideConversations(ticketId: string, enabled = true) {
  return useExtra<SideConversation[]>(
    ticketId,
    "side",
    "side-conversations",
    (d) =>
      listOf<SideConversation>(d)
        .filter((s) => s && s.id)
        .map((s) => ({ ...s, messages: Array.isArray(s.messages) ? s.messages : [] })),
    enabled,
  );
}
export function useStartSideEmail(ticketId: string) {
  return useExtraMutation(
    ticketId,
    ["side"],
    (v: { to: string[]; subject: string; body: string }, h) =>
      api.post(`${t(ticketId)}/side-conversations`, v, h),
    { creating: true },
  );
}
export function useStartSideSlack(ticketId: string) {
  return useExtraMutation(
    ticketId,
    ["side"],
    (v: { channel: string; body: string }, h) =>
      api.post(`${t(ticketId)}/side-conversations/slack`, v, h),
    { creating: true },
  );
}
export function useSideReply(ticketId: string) {
  return useExtraMutation(
    ticketId,
    ["side"],
    (v: { side_id: string; body: string }, h) =>
      api.post(`side-conversations/${encodeURIComponent(v.side_id)}/messages`, { body: v.body }, h),
    { creating: true },
  );
}

/* ---------- Jira ---------- */

export function useJira(ticketId: string, enabled = true) {
  return useExtra<JiraIssue[]>(
    ticketId,
    "jira",
    "jira",
    (d) => listOf<JiraIssue>(d).filter((j) => j && j.key),
    enabled,
  );
}
export function useCreateJira(ticketId: string) {
  return useExtraMutation(
    ticketId,
    ["jira"],
    (v: { project?: string; summary?: string; issue_type?: string }, h) =>
      api.post(`${t(ticketId)}/jira`, v, h),
    { creating: true },
  );
}
export function useLinkJira(ticketId: string) {
  return useExtraMutation(
    ticketId,
    ["jira"],
    (v: { key: string }, h) => api.post(`${t(ticketId)}/jira/link`, v, h),
    { creating: true },
  );
}
export function useUnlinkJira(ticketId: string) {
  return useExtraMutation(ticketId, ["jira"], (key: string) =>
    api.delete(`${t(ticketId)}/jira/${encodeURIComponent(key)}`),
  );
}

/* ---------- orders and payments ---------- */

function commerce(d: unknown): CommerceCard {
  const o = obj(d);
  const items = listOf<Record<string, unknown>>(o.orders ?? o.payments ?? o.items ?? d).filter(
    (x) => x && typeof x === "object",
  );
  return {
    available: o.available !== false,
    message: typeof o.message === "string" ? o.message : null,
    items,
  };
}
export function useOrders(ticketId: string) {
  return useExtra<CommerceCard>(ticketId, "orders", "orders", commerce);
}
export function usePayments(ticketId: string) {
  return useExtra<CommerceCard>(ticketId, "payments", "payments", commerce);
}

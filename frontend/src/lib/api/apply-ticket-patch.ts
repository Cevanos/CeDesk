import type { Priority, Ticket, TicketStatus } from "@/lib/api/types";

export type TicketPatch = Partial<{
  status: TicketStatus;
  priority: Priority;
  type_id: string | null;
  team_id: string | null;
  assignee_id: string | null;
  subject: string;
  fields: Record<string, string | number>;
  add_tags: string[];
  remove_tags: string[];
}>;

/**
 * Applies a PATCH body to a cached ticket, for optimistic updates. Derived
 * display fields (assignee_name, team_name) are left alone; the refetch that
 * follows every mutation replaces them with the server's values.
 */
export function applyTicketPatch(ticket: Ticket, patch: TicketPatch): Ticket {
  const next: Ticket = { ...ticket };
  if (patch.status !== undefined) next.status = patch.status;
  if (patch.priority !== undefined) next.priority = patch.priority;
  if (patch.subject !== undefined) next.subject = patch.subject;
  if (patch.type_id !== undefined) next.type_id = patch.type_id;
  if (patch.team_id !== undefined) next.team_id = patch.team_id;
  if (patch.assignee_id !== undefined) next.assignee_id = patch.assignee_id;
  if (patch.fields) next.fields = { ...ticket.fields, ...patch.fields };

  if (patch.add_tags || patch.remove_tags) {
    const removed = new Set(patch.remove_tags ?? []);
    const tags = (ticket.tags ?? []).filter((t) => !removed.has(t));
    for (const t of patch.add_tags ?? []) if (!tags.includes(t)) tags.push(t);
    next.tags = tags;
  }
  return next;
}

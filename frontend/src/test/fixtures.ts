import type { Ticket, TicketSummary } from "@/lib/api/types";

export function makeSummary(over: Partial<TicketSummary> = {}): TicketSummary {
  return {
    id: "t1",
    label: "T-1001",
    subject: "Cannot log in",
    status: "open",
    priority: "high",
    channel: "email",
    type_name: null,
    team_name: null,
    assignee_name: null,
    contact: { name: "Ada Lovelace", email: "ada@example.com" },
    tags: [],
    created_at: "2026-01-01T10:00:00Z",
    updated_at: "2026-01-01T11:00:00Z",
    ...over,
  };
}

export function makeTicket(over: Partial<Ticket> = {}): Ticket {
  return {
    ...makeSummary(),
    messages: [],
    older_messages_cursor: null,
    handover: null,
    ...over,
  };
}

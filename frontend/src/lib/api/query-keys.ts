import type { TicketFilters } from "@/lib/api/ticket-filters";

/**
 * Every TanStack Query key in the app, in one place. Build keys here and
 * invalidate by the broadest prefix that is correct, so a typo can never
 * silently create a cache entry that nothing ever invalidates.
 */
export const queryKeys = {
  me: ["me"] as const,

  notifications: {
    all: ["notifications"] as const,
    list: (unread = false) => ["notifications", { unread }] as const,
  },

  agents: {
    all: ["agents"] as const,
  },

  members: {
    all: ["members"] as const,
    list: ["members", "list"] as const,
    options: ["members", "options"] as const,
  },

  teams: { all: ["teams"] as const },
  ticketTypes: { all: ["ticket-types"] as const },
  routingDefaults: { all: ["routing-defaults"] as const },
  sla: { all: ["sla"] as const },
  savedReplies: {
    all: ["saved-replies"] as const,
    search: (q: string) => ["saved-replies", "search", q] as const,
  },
  rules: { all: ["rules"] as const },
  macros: { all: ["macros"] as const },
  emailAddresses: { all: ["email-addresses"] as const },
  failed: {
    emails: ["failed", "emails"] as const,
    events: ["failed", "events"] as const,
  },
  cevanos: { all: ["cevanos"] as const },

  tickets: {
    all: ["tickets"] as const,
    lists: ["tickets", "list"] as const,
    list: (filters: TicketFilters) => ["tickets", "list", filters] as const,
    detail: (id: string) => ["tickets", "detail", id] as const,
    attachments: (id: string) => ["tickets", "attachments", id] as const,
  },

  contacts: {
    tickets: (id: string) => ["contacts", id, "tickets"] as const,
  },
} as const;

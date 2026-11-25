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
  flows: {
    all: ["flows"] as const,
    detail: (id: string) => ["flows", "detail", id] as const,
    versions: (id: string) => ["flows", "versions", id] as const,
    validate: (graph: string) => ["flows", "validate", graph] as const,
  },
  automation: {
    catalog: ["automation", "catalog"] as const,
    runs: (filters: object) => ["automation", "runs", filters] as const,
    run: (id: string) => ["automation", "run", id] as const,
  },
  macros: { all: ["macros"] as const },
  emailAddresses: { all: ["email-addresses"] as const },
  failed: {
    emails: ["failed", "emails"] as const,
    events: ["failed", "events"] as const,
  },
  cevanos: { all: ["cevanos"] as const },
  reports: {
    team: (p: object) => ["reports", "team", p] as const,
    articles: (p: object) => ["reports", "articles", p] as const,
    options: ["reports", "options"] as const,
    backlog: (p: object) => ["reports", "backlog", p] as const,
    run: (p: object) => ["reports", "run", p] as const,
    saved: ["reports", "saved"] as const,
    live: (p: object) => ["reports", "live", p] as const,
    dashboards: ["reports", "dashboards"] as const,
    card: (p: object) => ["reports", "card", p] as const,
    schedules: ["reports", "schedules"] as const,
  },
  imports: {
    history: ["imports", "history"] as const,
    run: (id: string) => ["imports", "run", id] as const,
    discover: (id: string) => ["imports", "discover", id] as const,
    failures: (id: string) => ["imports", "failures", id] as const,
  },
  migration: {
    report: (id: string) => ["migration", "report", id] as const,
    cutover: (id: string) => ["migration", "cutover", id] as const,
  },
  replays: { detail: (id: string) => ["replays", id] as const },
  receipts: {
    settings: ["receipts", "settings"] as const,
    report: (p: object) => ["receipts", "report", p] as const,
  },
  views: { all: ["views"] as const },
  autoClose: { all: ["auto-close"] as const },
  triage: { all: ["triage-settings"] as const },
  portalSettings: { all: ["portal-settings"] as const },
  helpCenter: {
    categories: ["help-center", "categories"] as const,
    articles: ["help-center", "articles"] as const,
    settings: ["help-center", "settings"] as const,
    translations: (id: string) => ["help-center", "translations", id] as const,
    domain: ["help-center", "domain"] as const,
  },

  /** Customer-facing pages. Kept apart from the agent keys on purpose. */
  portal: {
    all: ["portal"] as const,
    tickets: (ws: string) => ["portal", ws, "tickets"] as const,
    ticket: (id: string) => ["portal", "ticket", id] as const,
    types: (ws: string) => ["portal", ws, "types"] as const,
  },
  public: {
    csat: (token: string) => ["public", "csat", token] as const,
    unsubscribe: (token: string) => ["public", "unsubscribe", token] as const,
    form: (ws: string) => ["public", ws, "form"] as const,
    help: (ws: string, q: string, locale: string) => ["public", ws, "help", q, locale] as const,
    article: (ws: string, id: string, locale: string) =>
      ["public", ws, "article", id, locale] as const,
  },

  tickets: {
    all: ["tickets"] as const,
    lists: ["tickets", "list"] as const,
    list: (filters: TicketFilters) => ["tickets", "list", filters] as const,
    detail: (id: string) => ["tickets", "detail", id] as const,
    attachments: (id: string) => ["tickets", "attachments", id] as const,
    followers: (id: string) => ["tickets", "followers", id] as const,
  },

  contacts: {
    all: ["contacts"] as const,
    tickets: (id: string) => ["contacts", id, "tickets"] as const,
    list: (p: object) => ["contacts", "list", p] as const,
    detail: (id: string) => ["contacts", id, "detail"] as const,
    notes: (id: string) => ["contacts", id, "notes"] as const,
    timeline: (id: string, kinds: string[]) => ["contacts", id, "timeline", kinds] as const,
    search: (q: string) => ["contacts", "search", q] as const,
  },
  campaigns: {
    all: ["campaigns"] as const,
    settings: ["campaigns", "settings"] as const,
    list: (state: string) => ["campaigns", "list", state] as const,
    detail: (id: string) => ["campaigns", id, "detail"] as const,
    audience: (id: string) => ["campaigns", id, "audience"] as const,
    stats: (id: string) => ["campaigns", id, "stats"] as const,
    recipients: (id: string, status: string) => ["campaigns", id, "recipients", status] as const,
  },
  suppressions: {
    all: ["suppressions"] as const,
    list: (p: object) => ["suppressions", "list", p] as const,
  },
  contactFields: { all: ["contact-fields"] as const },
  segments: {
    all: ["segments"] as const,
    detail: (id: string) => ["segments", id, "detail"] as const,
    contacts: (id: string) => ["segments", id, "contacts"] as const,
  },
} as const;

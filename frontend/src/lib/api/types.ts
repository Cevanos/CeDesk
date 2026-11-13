/** Domain types for the CDesk API. Source: CDESK_FRONTEND_GUIDE.md */

export type Role = "light_agent" | "agent" | "supervisor" | "admin";
export type TicketStatus = "open" | "pending" | "resolved";
export type Priority = "urgent" | "high" | "medium" | "low";
export type AgentStatus = "available" | "on_break" | "transfers_only" | "offline";
export type ChannelGroup = "email" | "chat" | "messaging";
/** `reply` is what you send; a sent reply comes back as `agent`. `ai` is Cevanos's AI. */
export type MessageKind = "customer" | "agent" | "ai" | "note" | "system" | "reply";
export type SlaState = "on_track" | "close" | "breached" | "met";
export type SlaKind = "first_reply" | "resolve";
export type Delivery = "pending" | "sent" | "failed";
export type ScanStatus = "not_scanned" | "clean" | "infected" | "scan_failed" | (string & {});

export interface SignInResponse {
  member_id: string;
  role: Role;
  expires_at: string;
}

export interface Me {
  id: string;
  name: string;
  email: string;
  role: Role;
  workspace_name?: string;
  workspace?: string;
  status?: AgentStatus | null;
}

export interface Contact {
  id?: string;
  name: string;
  email: string;
  vip?: boolean;
  customer_since?: string | null;
  other_tickets?: number;
  /** Phase 2 (CSAT): average rating 1-5 and how many ratings it is based on. */
  csat_average?: number | null;
  csat_count?: number;
}

/** A customer's satisfaction rating of a resolved ticket. */
export interface Csat {
  score: number;
  comment?: string | null;
  rated_at?: string | null;
}

export interface SlaClock {
  target_minutes: number;
  due_at: string;
  stopped_at: string | null;
  state: SlaState;
  /** Business minutes; negative once overdue. */
  minutes_left: number;
}

export interface TicketSla {
  policy: string | null;
  current: SlaKind;
  state: SlaState;
  paused: boolean;
  first_reply: SlaClock;
  resolve: SlaClock;
}

export interface Handover {
  summary?: string;
  reason?: string;
  channel_kind?: string;
  order_id?: string;
  verified?: string;
  [k: string]: unknown;
}

export interface TicketSummary {
  id: string;
  label: string;
  subject: string;
  status: TicketStatus;
  priority: Priority;
  channel: string | null;
  type_name: string | null;
  team_name: string | null;
  assignee_name: string | null;
  contact: Contact;
  tags: string[];
  created_at: string;
  updated_at: string;
  topic?: string | null;
  sentiment?: string | null;
  language?: string | null;
  source?: string | null;
  sla?: TicketSla | null;
  /** False on a handover ticket until an agent clicks Accept. */
  accepted?: boolean;
}

export interface TicketList {
  items: TicketSummary[];
  next_cursor: string | null;
  counts: Partial<Record<TicketStatus, number>>;
}

export interface Message {
  id: string;
  kind: MessageKind;
  body: string;
  author_member_id: string | null;
  author_name?: string | null;
  mention_ids?: string[];
  attachments?: MessageFile[];
  /** Replies on email tickets only. */
  delivery?: Delivery | null;
  created_at: string;
}

export interface MessageFile {
  id: string;
  name: string;
  size: number;
  mime: string;
  scan_status: ScanStatus;
}

export interface Ticket extends TicketSummary {
  description?: string | null;
  type_id?: string | null;
  team_id?: string | null;
  assignee_id?: string | null;
  fields?: Record<string, string | number | null>;
  reopen_count?: number;
  /** Phase 2: the customer's rating, or null when not rated. */
  csat?: Csat | null;
  messages: Message[];
  older_messages_cursor: string | null;
  handover: Handover | null;
}

/** GET /tickets/{id}/messages: newest first. */
export interface MessagePage {
  messages: Message[];
  next_cursor: string | null;
}

export interface Attachment {
  id: string;
  name: string;
  size?: number;
  mime?: string;
  scan_status?: ScanStatus;
  message_id?: string | null;
  created_at?: string;
}

export interface NotificationItem {
  id: string;
  kind: "assigned" | "mention" | "sla_breach" | (string & {});
  text: string;
  ticket_id: string | null;
  read: boolean;
  created_at: string;
}
export interface NotificationList {
  items: NotificationItem[];
  unread: number;
}

export interface AgentRow {
  member_id: string;
  name: string;
  role: Role;
  status: AgentStatus;
  open_tickets: number;
  channels: ChannelGroup[];
  capacity: Partial<Record<ChannelGroup, number>> | number | null;
  skills: string[];
}

export interface Member {
  id: string;
  name: string;
  email: string;
  role: Role;
  is_active: boolean;
  locked?: boolean;
}
export interface MemberList {
  items: Member[];
  next_after: string | null;
}

export interface Team {
  id: string;
  name: string;
  auto_assign: boolean;
  max_open_per_agent: number;
  member_ids: string[];
}

export interface TicketTypeField {
  key: string;
  label: string;
  kind: "text" | "number" | "select";
  options?: string[];
  required?: boolean;
}
export interface TicketType {
  id: string;
  name: string;
  fields: TicketTypeField[];
  required_skill?: string | null;
  /** Phase 2: customers can pick this type on the portal and web form. */
  for_customers?: boolean;
}

export interface ContactTickets {
  contact: Contact & { id: string };
  items: TicketSummary[];
  next_cursor?: string | null;
}

export interface SlaTarget {
  priority: Priority;
  first_reply_minutes: number;
  resolve_minutes: number;
  reads_as?: string;
  is_default?: boolean;
}
export interface SlaPolicy {
  id?: string;
  position: number;
  name: string;
  enabled: boolean;
  vip_only: boolean;
  channels: string[];
  priorities: string[];
  first_reply_minutes: number | null;
  resolve_minutes: number | null;
}
export interface Holiday {
  id?: string;
  /** YYYY-MM-DD */
  day: string;
  name: string;
}
export interface SlaSettings {
  targets: SlaTarget[];
  policies: SlaPolicy[];
  policies_are_default: boolean;
  calendar: {
    timezone: string;
    days: number[];
    opens: string;
    closes: string;
    always_open: boolean;
    holidays: Holiday[];
    is_default: boolean;
  };
}

export interface SavedReply {
  id: string;
  shortcut: string;
  body: string;
  source?: string;
}

export type RuleEvent = "created" | "customer_replied" | "sla_breached";
export interface Rule {
  id: string;
  name: string;
  enabled: boolean;
  position: number;
  event: RuleEvent;
  conditions: Record<string, unknown>[];
  actions: Record<string, unknown>[];
  problems: string[];
}
export interface Macro {
  id: string;
  name: string;
  position: number;
  steps: Record<string, unknown>[];
  problems: string[];
}

export interface EmailAddress {
  id: string;
  address: string;
  name: string;
  signature: string;
  is_default: boolean;
  /** Phase 2: forwarding and DNS checks. */
  forwarding?: ForwardingState | null;
  dns?: DnsState | null;
  dkim_selector?: string | null;
  /** The CDesk inbox to forward this address to. */
  forward_to?: string | null;
}

/** `waiting` = a check mail was sent and has not come back yet. */
export interface ForwardingState {
  status: "verified" | "not_checked" | "waiting" | (string & {});
  /** The address customers write to (X in "Forward X to Y"). */
  forward_from?: string | null;
  /** The CDesk inbox it must be forwarded to (Y). */
  forward_to?: string | null;
  checked_at?: string | null;
}
export interface DnsCheck {
  status: "pass" | "warn" | "fail" | "missing" | "not_checked" | (string & {});
  note?: string | null;
  /** The DNS record to publish, when one is needed. */
  record?: string | null;
}
export interface DnsState {
  spf?: DnsCheck | null;
  dkim?: DnsCheck | null;
  dmarc?: DnsCheck | null;
  checked_at?: string | null;
}

export interface FailedEmail {
  id: string;
  ticket_id: string | null;
  ticket_label: string | null;
  to: string[];
  subject: string;
  attempts: number;
  last_error: string | null;
  can_retry: boolean;
  created_at: string;
}
export interface FailedEvent {
  id: string;
  event: string;
  ticket_id: string | null;
  occurred_at: string;
  failed_at: string;
  attempts: number;
  last_error: string | null;
}

export interface CevanosConnection {
  connected: boolean;
  readable: boolean;
  api_key_hint: string | null;
  close_on_resolve: boolean;
  team_id: string | null;
  team_name: string | null;
  updated_at: string | null;
  webhook_path: string;
}

/* ------------------------------------------------------------------ *
 * Phase 2. Shapes are inferred from the backend's phase 2 notes
 * and read tolerantly; confirm field names against the API /docs page.
 * ------------------------------------------------------------------ */

export interface ReportSummary {
  created: number | null;
  resolved: number | null;
  open: number | null;
  first_reply_minutes: number | null;
  resolve_minutes: number | null;
  sla_met_pct: number | null;
  csat_average: number | null;
  csat_count: number | null;
}
export interface ReportTeamRow {
  id: string | null;
  name: string;
  created: number | null;
  resolved: number | null;
  open: number | null;
  sla_met_pct: number | null;
  csat_average: number | null;
}
export interface ReportAgentRow {
  id: string | null;
  name: string;
  team_name: string | null;
  resolved: number | null;
  open: number | null;
  first_reply_minutes: number | null;
  csat_average: number | null;
}
export interface TeamReport {
  from: string | null;
  to: string | null;
  summary: ReportSummary;
  /** Index 0 = 1 star ... index 4 = 5 stars. */
  csat: { score: number; count: number }[];
  teams: ReportTeamRow[];
  agents: ReportAgentRow[];
}

/** Topic name -> keywords. `redact_cards` masks card numbers in stored messages. */
export interface TriageSettings {
  topics: Record<string, string[]>;
  redact_cards: boolean;
  is_default?: boolean;
  /** How the API spells `topics`, so a save sends the same shape back. */
  shape?: "map" | "list";
}

export interface ReportArticleRow {
  id: string | null;
  title: string;
  helpful: number;
  not_helpful: number;
}

export interface PortalSettings {
  allowed_domains: string[];
  csat_survey: boolean;
  web_form: boolean;
}

export type ArticleStatus = "draft" | "published";
export interface ArticleCategory {
  id: string;
  name: string;
  position?: number;
}
export interface Article {
  id: string;
  category_id: string | null;
  category_name?: string | null;
  title: string;
  body: string;
  status: ArticleStatus;
  helpful_yes?: number;
  helpful_no?: number;
  updated_at?: string | null;
}

/* Customer-facing (no agent login) */

export interface PublicCsat {
  workspace_name?: string | null;
  ticket_label?: string | null;
  subject?: string | null;
  /** Set when the ticket was already rated. */
  score?: number | null;
  comment?: string | null;
  already_rated?: boolean;
}

export interface PortalMessage {
  id: string;
  /** True for the signed-in customer's own messages. */
  from_customer: boolean;
  body: string;
  /** Ready to show: "You", "Acme assistant", "Neha · Acme Support". */
  author?: string | null;
  created_at: string;
  attachments?: PortalAttachment[];
}
export interface PortalAttachment {
  id: string;
  name: string;
  size?: number;
  mime?: string;
  scan_status?: ScanStatus;
}
export interface PortalTicketSummary {
  id: string;
  label: string;
  subject: string;
  status: TicketStatus;
  /** What to show the customer, e.g. "Waiting on you" for Pending. */
  status_label?: string | null;
  created_at: string;
  updated_at: string;
}
export interface PortalTicket extends PortalTicketSummary {
  messages: PortalMessage[];
  csat?: Csat | null;
  /** The survey is on and the ticket is resolved. */
  can_rate?: boolean;
  workspace_name?: string | null;
}
export interface PortalSession {
  email?: string;
  name?: string | null;
  expires_at?: string;
}
export interface PublicForm {
  workspace_name?: string | null;
  enabled?: boolean;
  types: { id: string; name: string }[];
}
export interface PublicHelp {
  workspace_name?: string | null;
  categories: ArticleCategory[];
  articles: Article[];
}

/* ---------- Imports from other help desks ---------- */

export type ImportKind = "contacts" | "tickets" | "articles" | "replies";

export interface ImportConnection {
  id: string;
  source: string;
  /** The account name at the source, e.g. "Acme Support". */
  label?: string | null;
  state?: string;
  /** Last 4 characters of the key. The key itself is never returned. */
  credentials_hint?: string | null;
  created_at?: string;
}

export interface ImportGroup {
  id: string;
  name: string;
  /** The team the API suggests for this group, matched by name. */
  suggested_team_id: string | null;
}
export interface ImportDiscovery {
  counts: Record<string, number>;
  groups: ImportGroup[];
  /** Source status -> our status, shown read-only. */
  status_map: { from: string; to: string }[];
  warnings: string[];
}
export interface ImportDryRun {
  will_create: Record<string, number>;
  already_imported: Record<string, number>;
  warnings: string[];
}

export type ImportRunState = "queued" | "running" | "done" | "failed" | (string & {});
export interface ImportRun {
  id: string;
  connection_id?: string | null;
  source: string;
  label?: string | null;
  state: ImportRunState;
  kinds: string[];
  /** Per kind: created / skipped, plus `files`, `sync` and the like. */
  counts: Record<string, Record<string, number> | number>;
  sync_on: boolean;
  last_sync_at: string | null;
  last_error: string | null;
  started_by?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

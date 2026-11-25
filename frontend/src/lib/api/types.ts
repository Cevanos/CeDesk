/** Domain types for the CeDesk API. Source: CEDESK_FRONTEND_GUIDE.md */

import type { Resolution } from "./differentiator-types";
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
export type ScanStatus =
  "not_scanned" | "clean" | "infected" | "scan_failed" | "erased" | (string & {});

/**
 * What the sign-in call answers. When `two_factor_required` is true there is no session yet:
 * ask for the code (POST auth/2fa). `password_expired` means only Change password works.
 */
export interface SignInResponse {
  member_id?: string;
  role?: Role;
  expires_at?: string;
  two_factor_required?: boolean;
  password_expired?: boolean;
}

/** What GET /me says this person may do (hide what `actions` does not list). */
export type AccessScope = "all" | "team_edit" | "team_only" | (string & {});
export interface Access {
  role?: Role;
  actions?: string[];
  scope?: AccessScope;
  /** Set for a role limited to one team (`scope: "team_only"`). */
  team_id?: string | null;
  team_name?: string | null;
  role_name?: string | null;
}

export interface Me {
  id: string;
  name: string;
  email: string;
  role: Role;
  workspace_name?: string;
  workspace?: string;
  workspace_id?: string;
  status?: AgentStatus | null;
  /** The workspace requires 2FA and this person has not set it up: send them to set it up. */
  two_factor_setup_required?: boolean;
  access?: Access;
  /** Cevanos is connected: show the AI buttons and cards only when this is true. */
  ai?: boolean;
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
  phone?: string | null;
  organization?: string | null;
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
  /** Phase 3 list chips: the ticket this one is a child of, how many children it has, where it was merged. */
  parent_id?: string | null;
  child_count?: number;
  merged_into_id?: string | null;
  jira_keys?: string[];
  /** Agent inbox tools: when a snoozed ticket wakes up (ISO, with zone), null when not snoozed. */
  snoozed_until?: string | null;
}

export interface TicketList {
  items: TicketSummary[];
  next_cursor: string | null;
  counts: Partial<Record<TicketStatus | "snoozed", number>>;
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
  /** Imported messages only; information only, never used for replies. */
  recipients?: { to?: string[]; cc?: string[]; bcc?: string[] } | null;
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
  fields?: Record<string, string | number | boolean | null | (string | number)[]>;
  reopen_count?: number;
  /** Phase 2: the customer's rating, or null when not rated. */
  csat?: Csat | null;
  messages: Message[];
  older_messages_cursor: string | null;
  handover: Handover | null;
  /** Who a reply will copy (read-only; the server decides it). */
  reply_cc?: string[];
  /** The resolution receipt, or null/missing when there is none (or the ticket was reopened). */
  resolution?: Resolution | null;
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
  kind:
    | "assigned"
    | "mention"
    | "sla_breach"
    | "handover"
    | "side_reply"
    | "snooze_ended"
    | "following"
    | (string & {});
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
  /** `null` for a light agent: they have no availability to show. */
  status: AgentStatus | null;
  open_tickets: number;
  /** Open tickets per channel group (for example `{ email: 2, chat: 1 }`). */
  open_by_channel?: Partial<Record<ChannelGroup, number>>;
  channels: ChannelGroup[];
  capacity: Partial<Record<ChannelGroup, number>> | number | null;
  skills: string[];
  /** `false` for a deactivated member. `GET /agents` lists everyone on purpose. */
  is_active?: boolean;
  /** Active and the role replies to customers, so tickets can be assigned to them. */
  assignable?: boolean;
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

export type RuleEvent = "created" | "customer_replied" | "sla_breached" | "snooze_ended";
export interface Rule {
  id: string;
  name: string;
  enabled: boolean;
  position: number;
  event: RuleEvent;
  conditions: Record<string, unknown>[];
  actions: Record<string, unknown>[];
  problems: string[];
  /** Phase 5 safeguards. */
  max_runs_per_hour?: number;
  max_runs_per_day?: number;
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
  /** The CeDesk inbox to forward this address to. */
  forward_to?: string | null;
}

/** `waiting` = a check mail was sent and has not come back yet. */
export interface ForwardingState {
  status: "verified" | "not_checked" | "waiting" | (string & {});
  /** The address customers write to (X in "Forward X to Y"). */
  forward_from?: string | null;
  /** The CeDesk inbox it must be forwarded to (Y). */
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
  /** The events the Cevanos webhook must be subscribed to. Older servers do not send it. */
  webhook_events?: string[];
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
  /** Open tickets already past an SLA target. */
  open_breaching: number | null;
  /** Share of ratings that were 4 or 5 stars, 0-100. */
  csat_happy_pct: number | null;
}
export interface ReportTeamRow {
  id: string | null;
  name: string;
  created: number | null;
  resolved: number | null;
  open: number | null;
  sla_met_pct: number | null;
  /** Share of the team's tickets answered within first-reply time; null if none measured. */
  first_reply_sla_met_percent?: number | null;
  /** How many tickets that percentage is out of. */
  first_reply_sla_measured?: number | null;
  csat_average: number | null;
}
export interface ReportAgentRow {
  id: string | null;
  name: string;
  team_name: string | null;
  role: string | null;
  status: string | null;
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
  /** Show the agent's first name to customers (default on). */
  show_owner: boolean;
}

export type ArticleStatus = "draft" | "published";
export interface ArticleCategory {
  id: string;
  name: string;
  position?: number;
  /** Names in other languages, by language code: `{ hi: "भुगतान" }`. */
  name_translations?: Record<string, string>;
}
export interface Article {
  id: string;
  category_id: string | null;
  category_name?: string | null;
  title: string;
  body: string;
  status: ArticleStatus;
  helpful_count?: number;
  not_helpful_count?: number;
  updated_at?: string | null;
  /** SEO text, in the article's own (default) language. */
  slug?: string | null;
  suggested_slug?: string | null;
  meta_title?: string | null;
  meta_description?: string | null;
  canonical_url?: string | null;
  /** Languages that exist for this article (agent list rows). */
  locales?: string[];
  /** Public answers: the language served, and whether it is a fallback. */
  locale?: string;
  fallback?: boolean;
  available_locales?: string[];
  default_locale?: string;
  alternates?: ArticleAlternate[];
}

export interface ArticleAlternate {
  locale: string;
  slug?: string | null;
}

export interface ArticleTranslation {
  locale: string;
  title: string;
  body: string;
  status: ArticleStatus;
  slug?: string | null;
  suggested_slug?: string | null;
  meta_title?: string | null;
  meta_description?: string | null;
  canonical_url?: string | null;
  updated_at?: string | null;
  updated_by_name?: string | null;
}

export interface HelpCenterSettings {
  default_locale: string;
  seo_indexable: boolean;
}

export type DomainStatus = "pending" | "verified" | "failed" | string;
export interface HelpCenterDomain {
  domain: string;
  status: DomainStatus;
  dns?: { type?: string; name?: string; value?: string } | null;
  last_error?: string | null;
  failed_checks?: number;
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
  /** Who handles it ("Priyanka", or the team's name), what it waits on, and the checked outcome. Any may be null. */
  owner_first_name?: string | null;
  pending_step?: string | null;
  outcome?: string | null;
}
export interface PortalTicket extends PortalTicketSummary {
  messages: PortalMessage[];
  /** The ticket type, e.g. "Shipping issue", and what the customer filled in (by field key). */
  type_id?: string | null;
  type_name?: string | null;
  fields?: Record<string, string | number | null> | null;
  csat?: Csat | null;
  /** The survey is on and the ticket is resolved. */
  workspace_name?: string | null;
}
export interface PortalSession {
  email?: string;
  name?: string | null;
  expires_at?: string;
}
/** One question a ticket type asks the customer (`FieldDefOut`). */
export interface TypeField {
  key: string;
  label: string;
  kind: "text" | "number" | "select";
  options: string[];
  required: boolean;
}
/** A ticket type the customer can pick, with the questions it asks. */
export interface PublicType {
  id: string;
  name: string;
  fields: TypeField[];
}
export interface PublicForm {
  workspace_name?: string | null;
  enabled?: boolean;
  types: PublicType[];
}
export interface PublicHelp {
  workspace_name?: string | null;
  categories: ArticleCategory[];
  articles: Article[];
  /** The language served and the workspace default. */
  locale?: string;
  default_locale?: string;
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
  outcome?: ImportOutcome | null;
  retry_attempts?: number;
  report?: ImportReport | null;
}

export type ImportOutcome =
  "in_progress" | "complete" | "complete_with_exceptions" | "failed" | (string & {});

export interface ImportReport {
  open_failures: number;
  losses: { key: string; count: number; meaning: string }[];
  warnings: string[];
  /** Per kind. `in_source` and `difference` are null when the source can't count. */
  reconciliation: Record<
    string,
    { in_source: number | null; accounted_for: number; difference: number | null }
  >;
}

export interface ImportFailure {
  id: string;
  kind: string;
  external_id: string;
  category: "not_found" | "temporary" | "incomplete" | (string & {});
  last_error: string | null;
  attempts: number;
  last_failed_at: string | null;
  status: "open" | "resolved" | "dismissed" | (string & {});
}
export interface ImportFailurePage {
  failures: ImportFailure[];
  next_cursor: string | null;
}

/** A saved ticket-list view (GET /views). `filters` are GET /tickets query params. */
export interface SavedView {
  id: string;
  name: string;
  filters: Record<string, string>;
  shared: boolean;
  owner_id: string;
  owner_name?: string | null;
  can_edit: boolean;
  created_at: string;
  updated_at: string;
}

export interface Follower {
  member_id: string;
  name: string;
  email?: string | null;
  is_active?: boolean;
  added_by?: string | null;
  added_by_name?: string | null;
  created_at: string;
}

export interface FollowerList {
  followers: Follower[];
  /** Whether the signed-in user follows the ticket. */
  following: boolean;
}

export type BulkAction =
  | { action: "assign"; assignee_id: string | null }
  | { action: "set_team"; team_id: string | null }
  | { action: "set_status"; status: TicketStatus }
  | { action: "set_priority"; priority: Priority }
  | { action: "add_tags"; tags: string[] }
  | { action: "remove_tags"; tags: string[] }
  | { action: "snooze"; until: string }
  | { action: "unsnooze" };

export interface BulkResultItem {
  ticket_id: string;
  ok: boolean;
  error: { code: string; message: string } | null;
}
export interface BulkResult {
  results: BulkResultItem[];
  succeeded: number;
  failed: number;
}

export interface AutoCloseSettings {
  enabled: boolean;
  after_days: number;
  notice: string | null;
  updated_at: string | null;
  updated_by_name: string | null;
}

export interface TranscriptResult {
  to: string;
  to_customer: boolean;
  messages: number;
}

/* ---------- Phase 6: email campaigns ---------- */

export type CampaignState =
  "draft" | "scheduled" | "sending" | "paused" | "sent" | "cancelled" | "failed";

export interface CampaignStats {
  recipients: number;
  queued: number;
  sending: number;
  sent: number;
  failed: number;
  bounced: number;
  skipped: number;
  cancelled: number;
  opened: number;
  opens: number;
  unsubscribed: number;
  complained: number;
}

export interface Campaign {
  id: string;
  name: string;
  state: CampaignState;
  segment_id: string | null;
  segment_name: string | null;
  subject: string;
  body: string;
  from_address_id: string | null;
  from_address: string | null;
  from_name: string | null;
  track_opens: boolean;
  scheduled_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
  created_by: string | null;
  created_by_name: string | null;
  created_at: string;
  updated_at: string;
  stats: CampaignStats;
  problems: string[];
}

export interface CampaignSettings {
  postal_address: string | null;
  updated_at: string | null;
  max_recipients: number;
  daily_cap: number;
  per_minute: number;
}

export interface CampaignAudience {
  in_segment: number;
  not_mailable: number;
  suppressed: number;
  recipients: number;
  sent_last_24h: number;
  queued: number;
  daily_cap: number;
  max_recipients: number;
  problem: string | null;
}

export interface CampaignPreview {
  subject: string;
  text: string;
  html: string;
  contact_id: string | null;
  contact_name: string | null;
}

export type RecipientStatus =
  "queued" | "sending" | "sent" | "failed" | "bounced" | "skipped" | "cancelled";

export interface CampaignRecipient {
  id: string;
  contact_id: string | null;
  /** Null for an erased customer. */
  email: string | null;
  name: string | null;
  status: RecipientStatus | (string & {});
  attempts: number;
  error: string | null;
  queued_at: string | null;
  sent_at: string | null;
  failed_at: string | null;
  bounced_at: string | null;
  first_opened_at: string | null;
  open_count: number;
  unsubscribed_at: string | null;
  complained_at: string | null;
}

export type SuppressionReason = "unsubscribed" | "hard_bounce" | "complaint" | "manual" | "erased";

export interface SuppressionEntry {
  id: string;
  /** Null for an erased customer. */
  email: string | null;
  reason: SuppressionReason;
  source: "link" | "one_click" | "bounce" | "complaint" | "admin" | "erase" | (string & {});
  campaign_id: string | null;
  campaign_name: string | null;
  note: string | null;
  created_by: string | null;
  created_by_name: string | null;
  created_at: string;
}

export interface PublicUnsubscribe {
  workspace_name: string | null;
  unsubscribed: boolean;
}

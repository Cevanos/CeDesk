/** Domain types for the CDesk API (Phase 1). Source: CDESK_FRONTEND_GUIDE.md */

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
  kind: "assigned" | "mention";
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
}

export interface ContactTickets {
  contact: Contact & { id: string };
  items: TicketSummary[];
  next_cursor?: string | null;
}

/* ---- Phase 1 additions ---- */

export interface SlaTarget {
  priority: Priority;
  first_reply_minutes: number;
  resolve_minutes: number;
  reads_as?: string;
  is_default?: boolean;
}
export interface SlaPolicy {
  position: number;
  name: string;
  enabled: boolean;
  vip_only: boolean;
  channels: string[];
  priorities: string[];
  first_reply_minutes: number | null;
  resolve_minutes: number | null;
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
    holidays: { day: string; name: string }[];
    is_default: boolean;
  };
}

export interface SavedReply {
  id: string;
  shortcut: string;
  body: string;
  source?: string;
}

export type RuleEvent = "created" | "customer_replied";
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

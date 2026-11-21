/** Phase 2: contacts, notes, contact fields, segments, merge, CSV import. */

export type AttributeValue = string | number | boolean | null;
export type Attributes = Record<string, AttributeValue>;

export type FieldKind = "text" | "number" | "date" | "select" | "checkbox";

export interface ContactField {
  id: string;
  key: string;
  label: string;
  kind: FieldKind;
  options: string[] | null;
  position: number;
  created_at?: string;
  updated_at?: string;
}

/** The field as `GET /contacts/{id}` lists it (no id). */
export type ContactFieldRef = Pick<ContactField, "key" | "label" | "kind" | "options">;

export interface ContactRow {
  id: string;
  name: string;
  email: string;
  phone: string | null;
  organization: string | null;
  vip: boolean;
  customer_since?: string | null;
  created_at?: string;
  ticket_count: number;
  open_ticket_count: number;
  last_activity_at: string | null;
  other_emails: string[];
  attributes: Attributes;
}

export interface ContactNote {
  id: string;
  author_id: string;
  author_name: string;
  body: string;
  created_at: string;
  updated_at: string;
  can_edit: boolean;
}

export interface ContactDetail extends ContactRow {
  erased_at: string | null;
  csat_average: number | null;
  csat_count: number;
  note_count: number;
  notes: ContactNote[];
  fields: ContactFieldRef[];
}

export type ContactSort = "name" | "recent" | "tickets";

/** The list's filters, as the screen holds them. */
export interface ContactFilters {
  q: string;
  sort: ContactSort;
  vip: boolean;
  has_open: boolean;
  created_from: string;
  created_to: string;
  /** Field key to value. */
  attributes: Record<string, string>;
}

/** The same filters as a segment stores them: only what is set. */
export interface SegmentFilters {
  q?: string;
  sort?: ContactSort;
  vip?: boolean;
  has_open?: boolean;
  created_from?: string;
  created_to?: string;
  attributes?: Record<string, AttributeValue>;
}

export interface Segment {
  id: string;
  name: string;
  filters: SegmentFilters;
  created_by?: string | null;
  created_by_name?: string | null;
  created_at?: string;
  updated_at?: string;
  contact_count?: number;
}

export type TimelineKind =
  "ticket_created" | "ticket_resolved" | "message" | "csat" | "note" | "merge";

export interface TimelineItem {
  kind: TimelineKind;
  at: string;
  id: string;
  ticket_id: string | null;
  ticket_number: number | null;
  ticket_subject: string | null;
  author: string | null;
  text: string | null;
  score: number | null;
  detail: string | null;
}

export interface MergeMoves {
  tickets: number;
  archived_tickets: number;
  ratings: number;
  notes: number;
  other_emails: number;
  portal_sessions: number;
  privacy_requests: number;
}

export interface MergePreview {
  survivor: { id: string; name: string; email: string };
  duplicate: { id: string; name: string; email: string };
  moves: MergeMoves;
  taken: string[];
  conflicts: { field: string; kept: unknown; dropped: unknown }[];
  can_merge: boolean;
  blocked: string | null;
  warnings: string[];
}

export interface ImportMapping {
  email?: string;
  name?: string;
  phone?: string;
  organization?: string;
  vip?: string;
  attributes?: Record<string, string>;
}

export type OnExisting = "fill_blanks" | "overwrite" | "skip";

export interface ContactImportResult {
  dry_run: boolean;
  headers: string[];
  mapping: ImportMapping;
  rows: number;
  created: number;
  updated: number;
  unchanged: number;
  error_count: number;
  errors: { line: number; reason: string }[];
}

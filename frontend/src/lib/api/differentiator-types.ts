/** Types for the differentiator features: migration readiness, replays and resolution receipts. */

/* ---------- #4 Migration readiness ---------- */

export type ReadinessStatus = "imports" | "needs_setup" | "cannot_move";

export interface PreMigrationItem {
  key: string;
  name: string;
  status: ReadinessStatus;
  count?: number | null;
  note?: string | null;
}
export interface PreMigration {
  source: string;
  items: PreMigrationItem[];
  totals: Partial<Record<ReadinessStatus, number>>;
  warnings: string[];
}

export type CheckStatus = "ok" | "problem" | "skipped";
export interface CutoverCheck {
  key: string;
  status: CheckStatus;
  detail?: string | null;
}
export interface Cutover {
  run_id: string;
  source: string;
  ready: boolean;
  checks: CutoverCheck[];
}

/* ---------- #3 Workflow debugger ---------- */

export type ReplayState = "queued" | "running" | "done" | "failed" | "cancelled";
export interface ReplaySample {
  ticket_id?: string | null;
  number?: number | string | null;
  subject?: string | null;
  changes?: Record<string, { old?: unknown; new?: unknown; from?: unknown; to?: unknown }> | null;
  said?: string[] | null;
}
export interface ReplayConflict {
  ticket_id?: string | null;
  number?: number | string | null;
  subject?: string | null;
  field: string;
  tested_value?: unknown;
  other_rule?: string | null;
  other_value?: unknown;
  winner?: string | null;
}
export interface Replay {
  id: string;
  name?: string | null;
  kind: "rule" | "workflow" | string;
  target_version?: number | null;
  state: ReplayState;
  summary?: string | null;
  checked: number;
  matched: number;
  would_change: number;
  would_wait?: number | null;
  conflicts: number;
  samples?: ReplaySample[] | null;
  conflict_samples?: ReplayConflict[] | null;
  error?: string | null;
  requested_by_name?: string | null;
  created_at?: string | null;
  finished_at?: string | null;
}

/* ---------- #5 Resolution receipts ---------- */

export type ReceiptMode = "off" | "optional" | "required";
export interface ReceiptSettings {
  mode: ReceiptMode;
  required_teams: string[];
  updated_by_name?: string | null;
  updated_at?: string | null;
}

export type Verified = "unverified" | "agent" | "shopify" | "stripe";
export interface Resolution {
  action_taken: string;
  request?: string | null;
  result?: string | null;
  pending_item?: string | null;
  verified: Verified;
  customer_notified?: boolean | null;
  verified_at?: string | null;
  resolved_by_name?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}
/** What the resolve form sends. */
export interface ResolutionInput {
  action_taken: string;
  request?: string;
  result?: string;
  pending_item?: string;
  verified?: "agent" | "unverified";
  customer_notified?: boolean;
}

export interface ResolutionCounts {
  resolved: number;
  with_receipt: number;
  verified: number;
  verified_agent: number;
  verified_shopify: number;
  verified_stripe: number;
  pending: number;
  notified: number;
}
export interface ResolutionRow extends ResolutionCounts {
  id: string;
  name: string;
}
export interface ResolutionsReport {
  total: ResolutionCounts;
  teams: ResolutionRow[];
  agents: ResolutionRow[];
}

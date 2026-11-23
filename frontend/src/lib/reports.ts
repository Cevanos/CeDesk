/** Phase 3 reporting: types for the API, plus the small pure helpers the screens share. */

export type Unit = "count" | "minutes" | "percent" | "score";
export type MetricKind = "period" | "now" | "backlog";
export type RangePreset =
  "last_7d" | "last_30d" | "last_90d" | "last_365d" | "this_month" | "last_month";

export const RANGE_PRESETS: { value: RangePreset; label: string }[] = [
  { value: "last_7d", label: "Last 7 days" },
  { value: "last_30d", label: "Last 30 days" },
  { value: "last_90d", label: "Last 90 days" },
  { value: "last_365d", label: "Last 365 days" },
  { value: "this_month", label: "This month" },
  { value: "last_month", label: "Last month" },
];

export type ReportRange = { preset: RangePreset } | { from: string; to: string };

export interface ReportFilters {
  team?: string;
  assignee?: string;
  priority?: string;
  type?: string;
  channel?: string;
  tag?: string;
  topic?: string;
  sentiment?: string;
  imported?: "any" | "exclude" | "only";
}

export interface ReportDefinition {
  metric: string;
  group_by?: string;
  filters?: ReportFilters;
  range?: ReportRange;
}

export interface MetricInfo {
  key: string;
  label: string;
  unit: Unit;
  kind: MetricKind;
}
export interface PresetInfo {
  key: string;
  label: string;
  definition: ReportDefinition;
}
export interface ReportOptions {
  metrics: MetricInfo[];
  group_by: string[];
  filters: string[];
  ranges: RangePreset[];
  max_groups: number;
  presets: PresetInfo[];
}

export interface ReportGroup {
  key: string | null;
  label: string;
  value: number | null;
  n: number;
}

export interface ReportResult {
  metric: string;
  label: string;
  unit: Unit;
  group_by: string;
  from: string | null;
  to: string | null;
  timezone: string;
  total: number | null;
  total_n: number | null;
  groups: ReportGroup[];
  truncated: boolean;
  incomplete: boolean;
  complete_from: string | null;
  first_day: string | null;
  notes: string[];
}

/** The body of POST /reports/run (a built-in or a definition, never both). */
export type RunBody = { preset: string; range?: ReportRange } | { definition: ReportDefinition };

export interface SavedReport {
  id: string;
  name: string;
  definition: ReportDefinition;
  shared: boolean;
  owner_id: string;
  owner_name: string | null;
  can_edit: boolean;
  created_at: string;
  updated_at: string;
}

export interface DashboardCard {
  id: string;
  position: number;
  report_id: string | null;
  preset: string | null;
  label: string;
}
export interface Dashboard {
  id: string;
  name: string;
  shared: boolean;
  owner_id: string;
  owner_name: string | null;
  can_edit: boolean;
  cards: DashboardCard[];
  created_at: string;
  updated_at: string;
}
export type CardInput = { preset: string } | { report_id: string };

export interface BacklogDay {
  day: string;
  open: number;
  pending: number;
  breaching: number;
  taken_at?: string;
}
export interface BacklogReport {
  from: string;
  to: string;
  team_id: string | null;
  timezone: string;
  first_day: string | null;
  days: BacklogDay[];
}

export interface LiveTeamRow {
  team_id: string | null;
  name: string;
  open: number;
  pending: number;
  snoozed: number;
  breached: number;
  at_risk: number;
}
export interface LiveTicket {
  id: string;
  label: string;
  subject: string;
  priority: string;
  team_id: string | null;
  assignee_id: string | null;
  created_at?: string;
  waiting_minutes?: number;
  sla_due_at?: string;
  minutes_left?: number;
}
export interface LiveReport {
  generated_at: string;
  team_id: string | null;
  window_minutes: number;
  total: LiveTeamRow;
  teams: LiveTeamRow[];
  waiting: LiveTicket[];
  due_soon: LiveTicket[];
  agents: { available: number; on_break: number; transfers_only: number; offline: number };
}

export type Cadence = "daily" | "weekly" | "monthly";
export interface ReportSchedule {
  id: string;
  name: string;
  target: { report_id: string | null; preset: string | null; label: string };
  range: RangePreset;
  cadence: Cadence;
  /** ISO: 1 = Monday ... 7 = Sunday. */
  weekday: number | null;
  day_of_month: number | null;
  hour: number;
  timezone: string;
  enabled: boolean;
  next_run_at: string | null;
  last_sent_at: string | null;
  last_error: string | null;
  owner_id: string;
  owner_name: string | null;
  recipients: { member_id: string; name: string }[];
  can_edit: boolean;
  is_recipient: boolean;
}

/* ---------- formatting ---------- */

export const DASH = "—";

/** 0 -> "0m", 90 -> "1h 30m", 2290 -> "38h 10m". */
export function duration(min: number): string {
  const t = Math.round(min);
  const h = Math.floor(t / 60);
  const m = t % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/** A value with its unit. `null` is "no data", never 0. */
export function formatValue(v: number | null | undefined, unit: Unit): string {
  if (v === null || v === undefined) return DASH;
  switch (unit) {
    case "minutes":
      return duration(v);
    case "percent":
      return `${Math.round(v * 10) / 10}%`;
    case "score":
      return `${Math.round(v * 100) / 100} / 5`;
    default:
      return v.toLocaleString();
  }
}

export function capitalize(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

export const TIME_GROUPS = ["day", "week", "month"] as const;
export function isTimeGroup(g: string | undefined): boolean {
  return g === "day" || g === "week" || g === "month";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-11-17" -> "17 Nov". Returns the input when it is not a date. */
export function shortDay(key: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(key);
  if (!m) return key;
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1] ?? ""}`;
}

/** Label of a time bucket: a day, "Week of 17 Nov" (its Monday) or "Nov 2026". */
export function bucketLabel(key: string | null, groupBy: string): string {
  if (!key) return DASH;
  if (groupBy === "week") return `Week of ${shortDay(key)}`;
  if (groupBy === "month") {
    const m = /^(\d{4})-(\d{2})/.exec(key);
    return m ? `${MONTHS[Number(m[2]) - 1] ?? ""} ${m[1]}` : key;
  }
  return shortDay(key);
}

/** For priority, channel and sentiment the label is the raw value: capitalise it. */
const RAW_LABEL_GROUPS = new Set(["priority", "channel", "sentiment"]);
export function groupLabel(g: ReportGroup, groupBy: string): string {
  if (isTimeGroup(groupBy)) return bucketLabel(g.key, groupBy);
  return RAW_LABEL_GROUPS.has(groupBy) && g.key !== null ? capitalize(g.label) : g.label;
}

/* ---------- dates ---------- */

const pad = (n: number) => String(n).padStart(2, "0");

/** YYYY-MM-DD in the viewer's local time. */
export function ymd(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function daysAgo(today: Date, n: number): string {
  const d = new Date(today);
  d.setDate(d.getDate() - n);
  return ymd(d);
}

/** Whole days from `from` to `to` inclusive (both YYYY-MM-DD). */
export function daysBetween(from: string, to: string): number {
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b)) return 0;
  return Math.round((b - a) / 86_400_000) + 1;
}

/** Every YYYY-MM-DD from `from` to `to`, both included. */
export function eachDay(from: string, to: string): string[] {
  const n = daysBetween(from, to);
  const start = Date.parse(`${from}T00:00:00Z`);
  const out: string[] = [];
  for (let i = 0; i < n && i < 800; i++) {
    const d = new Date(start + i * 86_400_000);
    out.push(`${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`);
  }
  return out;
}

/** The date part of an ISO timestamp, as written (so in the offset it carries). */
export function datePart(iso: string): string {
  return iso.slice(0, 10);
}

/** A chart point: `null` is a gap, never a 0. */
export interface Point {
  key: string;
  label: string;
  value: number | null;
}

/** Fills the days missing between the first and last key with gaps. */
export function fillDayGaps(groups: ReportGroup[]): ReportGroup[] {
  const keyed = groups.filter((g) => g.key && /^\d{4}-\d{2}-\d{2}$/.test(g.key));
  if (keyed.length < 2) return groups;
  const have = new Map(keyed.map((g) => [g.key as string, g]));
  const days = eachDay(keyed[0]!.key as string, keyed[keyed.length - 1]!.key as string);
  return days.map((d) => have.get(d) ?? { key: d, label: d, value: null, n: 0 });
}

/* ---------- builder rules ---------- */

/** Group-by choices a metric allows (`open_now` has no time groups; `backlog` is by day). */
export function allowedGroupBy(kind: MetricKind | undefined, all: string[]): string[] {
  if (kind === "backlog") return all.filter((g) => g === "day");
  if (kind === "now") return all.filter((g) => !isTimeGroup(g));
  return all;
}

export const FILTER_KEYS = [
  "team",
  "assignee",
  "priority",
  "type",
  "channel",
  "tag",
  "topic",
  "sentiment",
  "imported",
] as const;

/** Drops empty filters so the body only carries what is set. */
export function cleanFilters(f: ReportFilters | undefined): ReportFilters | undefined {
  if (!f) return undefined;
  const out: Record<string, string> = {};
  for (const k of FILTER_KEYS) {
    const v = f[k];
    if (typeof v === "string" && v.trim() !== "" && !(k === "imported" && v === "any")) {
      out[k] = v.trim();
    }
  }
  return Object.keys(out).length ? (out as ReportFilters) : undefined;
}

/** What the server accepts for this metric: drops what its rules refuse. */
export function normalizeDefinition(
  d: ReportDefinition,
  kind: MetricKind | undefined,
): ReportDefinition {
  const out: ReportDefinition = { metric: d.metric };
  let filters = cleanFilters(d.filters);
  if (kind === "backlog") {
    out.group_by = "day";
    filters = filters?.team ? { team: filters.team } : undefined;
  } else if (d.group_by && d.group_by !== "none") {
    out.group_by = d.group_by;
  }
  if (kind === "now" && out.group_by && isTimeGroup(out.group_by)) delete out.group_by;
  if (filters) out.filters = filters;
  if (kind !== "now" && d.range) out.range = d.range;
  return out;
}

/** `true` when the range is a preset (a saved report needs one). */
export function hasPresetRange(r: ReportRange | undefined): r is { preset: RangePreset } {
  return !!r && "preset" in r;
}

/* ---------- files ---------- */

/** File name for a builder CSV when the server sends none. */
export function csvName(metric: string, groupBy: string | undefined, today = new Date()): string {
  return `cedesk-report-${metric}-${groupBy || "none"}-${ymd(today)}.csv`;
}

/* ---------- running at most N things at once ---------- */

/** Runs tasks in order, at most `max` at a time (a dashboard runs 3 cards, not 20). */
export function createLimiter(max: number) {
  let active = 0;
  const queue: (() => void)[] = [];
  const next = () => {
    if (active >= max) return;
    const run = queue.shift();
    if (run) run();
  };
  return function limit<T>(task: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      queue.push(() => {
        active++;
        task()
          .then(resolve, reject)
          .finally(() => {
            active--;
            next();
          });
      });
      next();
    });
  };
}

/* ---------- schedules ---------- */

export const WEEKDAYS = [
  { value: 1, label: "Monday" },
  { value: 2, label: "Tuesday" },
  { value: 3, label: "Wednesday" },
  { value: 4, label: "Thursday" },
  { value: 5, label: "Friday" },
  { value: 6, label: "Saturday" },
  { value: 7, label: "Sunday" },
];

const hh = (h: number) => `${pad(h)}:00`;

function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] ?? s[v] ?? s[0]}`;
}

/** "Every Monday at 09:00 (Asia/Kolkata)". */
export function describeSchedule(s: {
  cadence: Cadence;
  weekday: number | null;
  day_of_month: number | null;
  hour: number;
  timezone: string;
}): string {
  const at = `at ${hh(s.hour)} (${s.timezone})`;
  if (s.cadence === "weekly") {
    const day = WEEKDAYS.find((d) => d.value === s.weekday)?.label ?? "week";
    return `Every ${day} ${at}`;
  }
  if (s.cadence === "monthly") return `On the ${ordinal(s.day_of_month ?? 1)} of each month ${at}`;
  return `Every day ${at}`;
}

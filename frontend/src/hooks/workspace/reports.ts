"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type {
  ReportAgentRow,
  ReportArticleRow,
  ReportSummary,
  ReportTeamRow,
  TeamReport,
} from "@/lib/api/types";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);

/** First of `keys` that holds a finite number (numeric strings count), else null. */
function num(o: Obj | undefined, ...keys: string[]): number | null {
  if (!o) return null;
  for (const k of keys) {
    const v = o[k];
    if (typeof v === "number" && Number.isFinite(v)) return v;
    if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  }
  return null;
}

function str(o: Obj | undefined, ...keys: string[]): string | null {
  if (!o) return null;
  for (const k of keys) {
    const v = o[k];
    if (typeof v === "string" && v) return v;
  }
  return null;
}

function rows(raw: Obj, ...keys: string[]): Obj[] {
  for (const k of keys) {
    const v = raw[k];
    if (Array.isArray(v)) return v.filter(isObj);
  }
  return [];
}

/** A ratio might arrive as 0-1; show it as a percentage. */
function pct(v: number | null): number | null {
  if (v === null) return null;
  return v > 0 && v <= 1 ? Math.round(v * 1000) / 10 : v;
}

function normalizeCsat(raw: unknown): { score: number; count: number }[] {
  const out = [1, 2, 3, 4, 5].map((score) => ({ score, count: 0 }));
  if (Array.isArray(raw)) {
    for (const r of raw) {
      if (!isObj(r)) continue;
      const score = num(r, "score", "rating", "stars");
      const count = num(r, "count", "n", "total");
      if (score && score >= 1 && score <= 5 && count !== null) out[score - 1]!.count = count;
    }
  } else if (isObj(raw)) {
    // { "1": 2, "2": 0, ... } or { "5": { count: 3 } }
    for (const [k, v] of Object.entries(raw)) {
      const score = Number(k);
      if (!Number.isInteger(score) || score < 1 || score > 5) continue;
      const count = isObj(v) ? num(v, "count", "n") : typeof v === "number" ? v : null;
      if (count !== null) out[score - 1]!.count = count;
    }
  }
  return out;
}

/**
 * The report endpoint's exact field names aren't pinned down yet, so accept the likely
 * spellings and fall back to nulls (shown as "—") instead of breaking the page.
 */
export function normalizeTeamReport(raw: unknown): TeamReport {
  const r: Obj = isObj(raw) ? raw : {};
  const period = isObj(r.period) ? r.period : r;
  const s: Obj = isObj(r.summary) ? r.summary : isObj(r.totals) ? r.totals : r;
  const csatRaw = r.csat ?? r.csat_distribution ?? (isObj(r.summary) ? r.summary.csat : undefined);
  const csatObj = isObj(csatRaw) ? csatRaw : undefined;

  const summary: ReportSummary = {
    created: num(s, "created", "tickets_created", "new_tickets"),
    resolved: num(s, "resolved", "tickets_resolved"),
    open: num(s, "open", "tickets_open", "open_tickets"),
    first_reply_minutes: num(
      s,
      "first_reply_minutes",
      "avg_first_reply_minutes",
      "first_reply_avg_minutes",
      "median_first_reply_minutes",
      "avg_first_response_minutes",
    ),
    resolve_minutes: num(
      s,
      "resolve_minutes",
      "avg_resolve_minutes",
      "avg_resolution_minutes",
      "resolution_minutes",
    ),
    // `first_reply_sla_met_percent` is already 0-100; the other spellings may be 0-1 ratios.
    sla_met_pct:
      num(s, "first_reply_sla_met_percent") ??
      pct(num(s, "sla_met_pct", "sla_met_percent", "sla_met", "sla_compliance")),
    csat_average: num(s, "csat_average", "csat_avg", "avg_csat") ?? num(csatObj, "average", "avg"),
    csat_count: num(s, "csat_count", "csat_responses") ?? num(csatObj, "ratings", "count", "total"),
    open_breaching: num(s, "open_breaching"),
    csat_happy_pct: num(csatObj, "happy_percent"),
  };

  const distribution = normalizeCsat(
    csatObj ? (csatObj.distribution ?? csatObj.by_score ?? csatObj.scores ?? csatObj) : csatRaw,
  );

  const teams: ReportTeamRow[] = rows(r, "by_team", "teams", "tickets_by_team").map((t) => ({
    id: str(t, "team_id", "id"),
    name: str(t, "team_name", "name") ?? "No team",
    created: num(t, "created", "tickets_created", "total"),
    resolved: num(t, "resolved", "tickets_resolved"),
    open: num(t, "open", "open_tickets"),
    sla_met_pct: pct(num(t, "sla_met_pct", "sla_met", "sla_compliance")),
    csat_average: num(t, "csat_average", "csat_avg", "avg_csat"),
  }));

  const agents: ReportAgentRow[] = rows(r, "agents", "members", "by_agent").map((a) => ({
    id: str(a, "member_id", "agent_id", "id"),
    name: str(a, "name", "member_name", "agent_name") ?? "Unknown",
    team_name: str(a, "team_name", "team"),
    role: str(a, "role"),
    status: str(a, "status"),
    resolved: num(a, "resolved", "tickets_resolved"),
    open: num(a, "open", "open_tickets"),
    first_reply_minutes: num(
      a,
      "first_reply_minutes",
      "avg_first_reply_minutes",
      "first_reply_avg_minutes",
      "avg_first_response_minutes",
    ),
    csat_average: num(a, "csat_average", "csat_avg", "avg_csat"),
  }));

  return {
    from: str(period, "from", "start"),
    to: str(period, "to", "end"),
    summary,
    csat: distribution,
    teams,
    agents,
  };
}

export interface ReportParams {
  /** YYYY-MM-DD */
  from: string;
  /** YYYY-MM-DD */
  to: string;
  team_id?: string;
}

const pad = (n: number) => String(n).padStart(2, "0");

/**
 * The API wants a time zone on `from` / `to` (e.g. 2026-11-01T00:00:00+05:30), so a plain
 * YYYY-MM-DD from the date picker becomes local start-of-day / end-of-day with the viewer's offset.
 */
export function withOffset(day: string, end = false): string {
  const [y, m, d] = day.split("-").map(Number);
  if (!y || !m || !d) return day;
  const at = new Date(y, m - 1, d, end ? 23 : 0, end ? 59 : 0, end ? 59 : 0);
  const off = -at.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  const abs = Math.abs(off);
  return (
    `${y}-${pad(m)}-${pad(d)}T${end ? "23:59:59" : "00:00:00"}` +
    `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
  );
}

/** Supervisors and Admins only; Agents get a 403 (the page shows a notice). */
export function useTeamReport(params: ReportParams) {
  return useQuery({
    queryKey: queryKeys.reports.team(params),
    queryFn: async () =>
      normalizeTeamReport(
        await api.get<unknown>("reports/team", {
          from: withOffset(params.from),
          to: withOffset(params.to, true),
          team_id: params.team_id,
        }),
      ),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

/** Help article votes ("Was this helpful?") for the chosen dates. */
export function normalizeArticleReport(raw: unknown): ReportArticleRow[] {
  const r: Obj = isObj(raw) ? raw : {};
  const list = Array.isArray(raw)
    ? raw.filter(isObj)
    : rows(r, "articles", "items", "rows", "data");
  return list.map((a) => ({
    id: str(a, "article_id", "id"),
    title: str(a, "title", "article_title", "name") ?? "Untitled article",
    helpful: num(a, "helpful", "helpful_yes", "yes", "helpful_count", "up") ?? 0,
    not_helpful: num(a, "not_helpful", "helpful_no", "no", "not_helpful_count", "down") ?? 0,
  }));
}

export function useArticleReport(params: ReportParams) {
  return useQuery({
    queryKey: queryKeys.reports.articles(params),
    queryFn: async () =>
      normalizeArticleReport(
        await api.get<unknown>("reports/articles", {
          from: withOffset(params.from),
          to: withOffset(params.to, true),
        }),
      ),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

/* ---------- More metrics (GET /reports/metrics) and the AI card (GET /reports/ai) ---------- */

/** Row of backlog / channel / topic / sentiment tables. `created` is absent for backlog. */
export interface Labelled {
  label: string;
  created: number | null;
  open: number;
}
export interface MetricTile {
  key: string;
  label: string;
  value: number | null;
  unit: "count" | "minutes" | "percent";
  hint?: string;
}
export interface MetricsAgentRow {
  id: string | null;
  name: string;
  role: string | null;
  logged_minutes: number | null;
  billable_minutes: number | null;
  open_tasks: number | null;
  overdue_tasks: number | null;
}
export interface MetricsReport {
  tiles: MetricTile[];
  backlog: Labelled[];
  channels: Labelled[];
  topics: Labelled[];
  sentiment: Labelled[];
  agents: MetricsAgentRow[];
  /** Null hides the Governance link. */
  governance_url: string | null;
}
export interface AiReport {
  chats: number | null;
  handed_over: number | null;
  /** 0-100 */
  handed_over_percent: number | null;
  resolved_by_ai: number | null;
  /** 0-100 */
  resolved_by_ai_percent: number | null;
  csat_average: number | null;
  csat_count: number | null;
}

/** Rows of `{ label | value, created?, open }`. */
function labelled(raw: unknown): Labelled[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(isObj).map((r) => ({
    label: str(r, "label", "bucket", "value") ?? "(none)",
    created: num(r, "created"),
    open: num(r, "open") ?? 0,
  }));
}

/** 0-1 ratio to a 0-100 percent with one decimal (the AI endpoint sends ratios). */
function ratioToPercent(v: number | null): number | null {
  return v === null ? null : Math.round(v * 1000) / 10;
}

export function normalizeMetricsReport(raw: unknown): MetricsReport {
  const r: Obj = isObj(raw) ? raw : {};
  const s: Obj = isObj(r.summary) ? r.summary : {};
  const of = (n: number | null, m: number | null) =>
    n !== null && m !== null ? `${n} of ${m}` : undefined;
  const tiles: MetricTile[] = [
    {
      key: "open",
      label: "Open now",
      value: num(s, "open"),
      unit: "count",
      // The API counts open tickets as of now, whatever dates are picked.
      hint: "As of now, not the date range",
    },
    {
      key: "one_touch",
      label: "One-touch",
      value: num(s, "one_touch_percent"),
      unit: "percent",
      hint: of(num(s, "one_touch"), num(s, "one_touch_measured"))?.concat(" resolved"),
    },
    {
      key: "reopened",
      label: "Reopened",
      value: num(s, "reopen_rate_percent"),
      unit: "percent",
      hint: of(num(s, "reopened"), num(s, "reopen_measured")),
    },
    {
      key: "time_logged",
      label: "Time logged",
      value: num(s, "time_logged_minutes"),
      unit: "minutes",
    },
    { key: "billable", label: "Billable", value: num(s, "billable_minutes"), unit: "minutes" },
  ];
  return {
    tiles,
    backlog: labelled(r.backlog_by_age),
    channels: labelled(r.channels),
    topics: labelled(r.topics),
    sentiment: labelled(r.sentiment),
    agents: rows(r, "agents").map((a) => ({
      id: str(a, "member_id"),
      name: str(a, "name") ?? "Unknown",
      role: str(a, "role"),
      logged_minutes: num(a, "logged_minutes"),
      billable_minutes: num(a, "billable_minutes"),
      open_tasks: num(a, "open_tasks"),
      overdue_tasks: num(a, "overdue_tasks"),
    })),
    governance_url: str(r, "governance_url"),
  };
}

export function normalizeAiReport(raw: unknown): AiReport {
  const root: Obj = isObj(raw) ? raw : {};
  const r: Obj = isObj(root.ai) ? root.ai : {};
  return {
    chats: num(r, "conversations"),
    handed_over: num(r, "handed_over"),
    handed_over_percent: ratioToPercent(num(r, "handover_rate")),
    resolved_by_ai: num(r, "ai_resolved"),
    resolved_by_ai_percent: ratioToPercent(num(r, "ai_resolved_rate")),
    csat_average: num(r, "ai_csat"),
    csat_count: num(r, "ai_csat_responses"),
  };
}

const range = (p: ReportParams) => ({ from: withOffset(p.from), to: withOffset(p.to, true) });

/** Supervisors and Admins only. */
export function useMetricsReport(params: ReportParams, enabled = true) {
  return useQuery({
    queryKey: ["reports", "metrics", params] as const,
    enabled,
    queryFn: async () =>
      normalizeMetricsReport(
        await api.get<unknown>("reports/metrics", { ...range(params), team_id: params.team_id }),
      ),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

export function useAiReport(params: ReportParams, enabled = true) {
  return useQuery({
    queryKey: ["reports", "ai", params] as const,
    enabled,
    queryFn: async () => normalizeAiReport(await api.get<unknown>("reports/ai", range(params))),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

/** Query for a CSV download of a report: same dates and team, `format=csv`. */
export function csvQuery(params: ReportParams, withTeam: boolean) {
  return {
    ...range(params),
    ...(withTeam && params.team_id ? { team_id: params.team_id } : {}),
    format: "csv",
  };
}

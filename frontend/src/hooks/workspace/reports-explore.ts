"use client";

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import { ApiError, api, listOf, type Query } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import {
  createLimiter,
  type BacklogReport,
  type CardInput,
  type Dashboard,
  type LiveReport,
  type ReportDefinition,
  type ReportOptions,
  type ReportRange,
  type ReportResult,
  type ReportSchedule,
  type RunBody,
  type SavedReport,
} from "@/lib/reports";

/**
 * A busy server answers 429 with `Retry-After`: wait that long and try once more,
 * then let the message through.
 */
export async function retryOnce<T>(fn: () => Promise<T>, wait = sleep): Promise<T> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError && e.status === 429) {
      await wait((e.retryAfter ?? 5) * 1000);
      return fn();
    }
    throw e;
  }
}
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** At most 3 report runs at a time, however many cards a dashboard has. */
const cardLimit = createLimiter(3);

const quiet = { meta: { silent: true } } as const;

/* ---------- options ---------- */

/** The metric, filter and built-in lists. They change with a release, so keep them all session. */
export function useReportOptions() {
  return useQuery({
    queryKey: queryKeys.reports.options,
    queryFn: () => api.get<ReportOptions>("reports/options"),
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

/* ---------- running ---------- */

/** What a result view runs: a body for POST /reports/run, or a saved report by id. */
export type RunSource = { kind: "body"; body: RunBody } | { kind: "saved"; id: string };

export function runPath(src: RunSource): string {
  return src.kind === "saved" ? `reports/saved/${src.id}/run` : "reports/run";
}
export function runBody(src: RunSource): unknown {
  return src.kind === "saved" ? undefined : src.body;
}

export function useRunReport(src: RunSource | null) {
  return useQuery({
    queryKey: queryKeys.reports.run(src ?? {}),
    enabled: src !== null,
    queryFn: () => retryOnce(() => api.post<ReportResult>(runPath(src!), runBody(src!))),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: false,
  });
}

/* ---------- backlog ---------- */

export function useBacklog(params: { from: string; to: string; team_id?: string }, enabled = true) {
  return useQuery({
    queryKey: queryKeys.reports.backlog(params),
    enabled,
    queryFn: () => retryOnce(() => api.get<BacklogReport>("reports/backlog", params)),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    retry: false,
  });
}

/* ---------- saved reports ---------- */

export function useSavedReports() {
  return useQuery({
    queryKey: queryKeys.reports.saved,
    queryFn: () => api.get<unknown>("reports/saved"),
    select: (d) => listOf<SavedReport>(d),
    staleTime: 30_000,
  });
}

function invalidateSaved(qc: QueryClient) {
  // A saved report can sit on dashboards and schedules, so they refresh too.
  void qc.invalidateQueries({ queryKey: queryKeys.reports.saved });
  void qc.invalidateQueries({ queryKey: queryKeys.reports.dashboards });
  void qc.invalidateQueries({ queryKey: queryKeys.reports.schedules });
}

export function useCreateSavedReport() {
  const qc = useQueryClient();
  return useMutation({
    ...quiet,
    mutationFn: (v: { name: string; definition: ReportDefinition; shared: boolean }) =>
      api.post<SavedReport>("reports/saved", v),
    onSuccess: () => invalidateSaved(qc),
  });
}

export function useUpdateSavedReport() {
  const qc = useQueryClient();
  return useMutation({
    ...quiet,
    mutationFn: ({
      id,
      ...patch
    }: {
      id: string;
      name?: string;
      definition?: ReportDefinition;
      shared?: boolean;
    }) => api.patch<SavedReport>(`reports/saved/${id}`, patch).then((r) => r.data),
    onSuccess: () => invalidateSaved(qc),
  });
}

export function useDeleteSavedReport() {
  const qc = useQueryClient();
  return useMutation({
    ...quiet,
    mutationFn: (id: string) => api.delete<void>(`reports/saved/${id}`),
    onSuccess: () => invalidateSaved(qc),
  });
}

/* ---------- dashboards ---------- */

/** Shared first, then the person's own (the API sorts them; this keeps it that way). */
export function useDashboards() {
  return useQuery({
    queryKey: queryKeys.reports.dashboards,
    queryFn: () => api.get<unknown>("dashboards"),
    select: (d) => listOf<Dashboard>(d),
    staleTime: 30_000,
  });
}

export function useDashboard(id: string) {
  return useQuery({
    queryKey: [...queryKeys.reports.dashboards, id] as const,
    queryFn: () => api.get<Dashboard>(`dashboards/${id}`),
    staleTime: 30_000,
  });
}

export function useCreateDashboard() {
  const qc = useQueryClient();
  return useMutation({
    ...quiet,
    mutationFn: (v: { name: string; shared: boolean; cards: CardInput[] }) =>
      api.post<Dashboard>("dashboards", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.reports.dashboards }),
  });
}

export function useUpdateDashboard() {
  const qc = useQueryClient();
  return useMutation({
    ...quiet,
    mutationFn: ({
      id,
      ...patch
    }: {
      id: string;
      name?: string;
      shared?: boolean;
      cards?: CardInput[];
    }) => api.patch<Dashboard>(`dashboards/${id}`, patch).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.reports.dashboards }),
  });
}

export function useDeleteDashboard() {
  const qc = useQueryClient();
  return useMutation({
    ...quiet,
    mutationFn: (id: string) => api.delete<void>(`dashboards/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.reports.dashboards }),
  });
}

export interface CardRunParams {
  range?: ReportRange;
  team_id?: string;
}

/** The JSON body of a card run: both parts are optional (`{}` means the card's own). */
export function cardBody(p: CardRunParams): CardRunParams {
  const body: CardRunParams = {};
  if (p.range) body.range = p.range;
  if (p.team_id) body.team_id = p.team_id;
  return body;
}

/** One card, run on its own, through the shared limit of 3 at a time. */
export function useRunCard(
  dashboardId: string,
  cardId: string,
  params: CardRunParams,
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.reports.card({ dashboardId, cardId, ...params }),
    enabled,
    queryFn: () =>
      cardLimit(() =>
        retryOnce(() =>
          api.post<ReportResult>(`dashboards/${dashboardId}/cards/${cardId}/run`, cardBody(params)),
        ),
      ),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    retry: false,
  });
}

/* ---------- live ---------- */

export interface LiveParams {
  team_id?: string;
  window_minutes: number;
}

export function useLiveReport(params: LiveParams) {
  return useQuery({
    queryKey: queryKeys.reports.live(params),
    queryFn: () => api.get<LiveReport>("reports/live", params as unknown as Query),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
    // The server shares one answer for about 5 s, so a quicker refetch buys nothing.
    staleTime: 5_000,
    retry: false,
  });
}

/* ---------- schedules ---------- */

export function useSchedules() {
  return useQuery({
    queryKey: queryKeys.reports.schedules,
    queryFn: () => api.get<unknown>("report-schedules"),
    select: (d) => listOf<ReportSchedule>(d),
    staleTime: 30_000,
  });
}

export interface ScheduleInput {
  name: string;
  report_id?: string;
  preset?: string;
  range: string;
  cadence: string;
  weekday?: number;
  day_of_month?: number;
  hour: number;
  recipient_ids: string[];
}

export function useCreateSchedule() {
  const qc = useQueryClient();
  return useMutation({
    ...quiet,
    mutationFn: (v: ScheduleInput) => api.post<ReportSchedule>("report-schedules", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.reports.schedules }),
  });
}

export function useUpdateSchedule() {
  const qc = useQueryClient();
  return useMutation({
    ...quiet,
    mutationFn: ({
      id,
      ...patch
    }: { id: string } & Partial<ScheduleInput> & { enabled?: boolean }) =>
      api.patch<ReportSchedule>(`report-schedules/${id}`, patch).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.reports.schedules }),
  });
}

export function useDeleteSchedule() {
  const qc = useQueryClient();
  return useMutation({
    ...quiet,
    mutationFn: (id: string) => api.delete<void>(`report-schedules/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.reports.schedules }),
  });
}

export function useLeaveSchedule() {
  const qc = useQueryClient();
  return useMutation({
    ...quiet,
    mutationFn: (id: string) => api.post<void>(`report-schedules/${id}/leave`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.reports.schedules }),
  });
}

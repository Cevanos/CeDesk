"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, api, createIdempotency, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type {
  ImportConnection,
  ImportDiscovery,
  ImportDryRun,
  ImportGroup,
  ImportKind,
  ImportRun,
} from "@/lib/api/types";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const strings = (v: unknown): string[] =>
  Array.isArray(v)
    ? v.map((x) => (isObj(x) ? (text(x.message) ?? "") : String(x))).filter(Boolean)
    : [];

function numbers(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (isObj(v)) {
    for (const [k, n] of Object.entries(v)) if (typeof n === "number") out[k] = n;
  }
  return out;
}

/** 4xx means the server understood and refused: the same Idempotency-Key must not be reused. */
const clientError = (e: unknown) => e instanceof ApiError && e.status >= 400 && e.status < 500;

/** The field names are not pinned down in the plan, so accept the likely spellings. */
export function normalizeDiscovery(raw: unknown): ImportDiscovery {
  const r: Obj = isObj(raw) ? raw : {};
  const groups: ImportGroup[] = listOf<Obj>(r.groups).map((g) => ({
    id: String(g.external_id ?? g.id ?? g.group_id ?? ""),
    name: text(g.name) ?? text(g.label) ?? String(g.external_id ?? g.id ?? ""),
    suggested_team_id:
      text(g.suggested_team_id) ?? text(g.team_id) ?? text(g.suggested_team) ?? null,
  }));
  const sm = r.status_map ?? r.statuses;
  const status_map = isObj(sm)
    ? Object.entries(sm).map(([from, to]) => ({ from, to: String(to) }))
    : listOf<Obj>(sm).map((s) => ({
        from: String(s.from ?? s.source ?? ""),
        to: String(s.to ?? s.target ?? ""),
      }));
  return {
    counts: numbers(r.counts),
    groups: groups.filter((g) => g.id),
    status_map,
    warnings: strings(r.warnings),
  };
}

export function normalizeDryRun(raw: unknown): ImportDryRun {
  const r: Obj = isObj(raw) ? raw : {};
  return {
    will_create: numbers(r.will_create),
    already_imported: numbers(r.already_imported),
    warnings: strings(r.warnings),
  };
}

export function normalizeRun(raw: unknown): ImportRun {
  const r: Obj = isObj(raw) && isObj(raw.run) ? raw.run : isObj(raw) ? raw : {};
  const counts: ImportRun["counts"] = {};
  if (isObj(r.counts)) {
    for (const [k, v] of Object.entries(r.counts)) {
      if (typeof v === "number") counts[k] = v;
      else if (isObj(v)) counts[k] = numbers(v);
    }
  }
  // History rows carry `result` (running / finished / syncing / failed) instead of `state`.
  const resultMap: Record<string, ImportRun["state"]> = {
    finished: "done",
    syncing: "done",
    failed: "failed",
    running: "running",
  };
  const result = text(r.result);
  const explicitState = text(r.state) ?? text(r.status);
  const state = (explicitState ??
    (result ? (resultMap[result] ?? result) : "queued")) as ImportRun["state"];
  return {
    id: String(r.id ?? ""),
    connection_id: text(r.connection_id),
    source: text(r.source) ?? "",
    label: text(r.label) ?? text(r.account),
    state,
    kinds: strings(r.kinds),
    counts,
    sync_on: r.sync_on === true || (!explicitState && result === "syncing"),
    last_sync_at: text(r.last_sync_at),
    last_error: text(r.last_error),
    started_by: text(r.started_by_name) ?? text(r.started_by),
    started_at: text(r.started_at) ?? text(r.created_at),
    finished_at: text(r.finished_at),
  };
}

export interface RunOptions {
  kinds?: ImportKind[];
  team_map?: Record<string, string | null>;
}

export function useConnectImport() {
  const [idem] = useState(createIdempotency);
  return useMutation({
    mutationFn: (v: {
      source: string;
      settings: Record<string, string>;
      credentials: Record<string, string>;
    }) => api.post<ImportConnection>("imports/connections", v, idem.header(JSON.stringify(v))),
    meta: { silent: true },
    onSuccess: () => idem.done(),
    onError: (e) => {
      if (clientError(e)) idem.done();
    },
  });
}

/** The CSV goes as the raw request body (not a form), named in the query string. */
export function useUploadImportCsv() {
  const [idem] = useState(createIdempotency);
  return useMutation({
    mutationFn: async (file: File) => {
      const print = `${file.name}:${file.size}:${file.lastModified}`;
      const res = await fetch(`/api/imports/csv?filename=${encodeURIComponent(file.name)}`, {
        method: "POST",
        headers: { "Content-Type": "text/csv", Accept: "application/json", ...idem.header(print) },
        credentials: "same-origin",
        body: file,
      });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        const err = isObj(body) && isObj(body.error) ? body.error : {};
        throw new ApiError({
          status: res.status,
          code: text(err.code) ?? "Internal",
          message: text(err.message) ?? "Something went wrong.",
          requestId: res.headers.get("x-request-id"),
        });
      }
      return body as ImportConnection;
    },
    meta: { silent: true },
    onSuccess: () => idem.done(),
    onError: (e) => {
      if (clientError(e)) idem.done();
    },
  });
}

export function useDiscoverImport(connectionId: string) {
  return useQuery({
    queryKey: queryKeys.imports.discover(connectionId),
    queryFn: async () =>
      normalizeDiscovery(await api.get<unknown>(`imports/connections/${connectionId}/discover`)),
    retry: false,
    staleTime: Infinity,
  });
}

export function useDryRunImport(connectionId: string) {
  return useMutation({
    mutationFn: async (v: RunOptions) =>
      normalizeDryRun(await api.post<unknown>(`imports/connections/${connectionId}/dry-run`, v)),
    meta: { silent: true },
  });
}

export function useStartImport(connectionId: string) {
  const qc = useQueryClient();
  const [idem] = useState(createIdempotency);
  return useMutation({
    mutationFn: async (v: RunOptions & { sync?: boolean }) =>
      normalizeRun(
        await api.post<unknown>(
          `imports/connections/${connectionId}/run`,
          v,
          idem.header(JSON.stringify(v)),
        ),
      ),
    meta: { silent: true },
    onSuccess: () => {
      idem.done();
      void qc.invalidateQueries({ queryKey: queryKeys.imports.history });
    },
    onError: (e) => {
      if (clientError(e)) idem.done();
    },
  });
}

export function useCloseImportConnection(connectionId: string) {
  return useMutation({
    mutationFn: () => api.post<void>(`imports/connections/${connectionId}/close`),
    meta: { silent: true },
  });
}

/** Polls every 4 s while the run is queued or running. */
export function useImportRun(id: string | null) {
  return useQuery({
    queryKey: queryKeys.imports.run(id ?? ""),
    enabled: !!id,
    queryFn: async () => normalizeRun(await api.get<unknown>(`imports/runs/${id}`)),
    refetchInterval: (q) =>
      q.state.data?.state === "queued" || q.state.data?.state === "running" ? 4_000 : false,
  });
}

function useRunAction(id: string, action: "sync-now" | "sync-stop") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<unknown>(`imports/runs/${id}/${action}`),
    meta: { silent: true },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.imports.run(id) });
      void qc.invalidateQueries({ queryKey: queryKeys.imports.history });
    },
  });
}
export const useSyncNow = (id: string) => useRunAction(id, "sync-now");
export const useSyncStop = (id: string) => useRunAction(id, "sync-stop");

export function useImportHistory() {
  return useQuery({
    queryKey: queryKeys.imports.history,
    queryFn: async () =>
      listOf<unknown>(await api.get<unknown>("imports/history")).map(normalizeRun),
    staleTime: 10_000,
  });
}

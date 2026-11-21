"use client";

import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, ApiError, downloadFile, listOf, type Query } from "@/lib/api/client";
import type {
  Attributes,
  ContactDetail,
  ContactField,
  ContactFilters,
  ContactImportResult,
  ContactNote,
  ContactRow,
  FieldKind,
  ImportMapping,
  MergePreview,
  OnExisting,
  Segment,
  SegmentFilters,
  TimelineItem,
} from "@/lib/api/contacts-types";
import { queryKeys } from "@/lib/api/query-keys";
import { filtersToQuery } from "@/lib/contacts";

const PAGE = 50;

/** A value that follows `value` after `ms` of quiet (search boxes). */
export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}

function pageOf<T>(raw: unknown, key: string): CursorPage<T> {
  const o = (raw ?? {}) as Record<string, unknown>;
  const items = Array.isArray(o[key]) ? (o[key] as T[]) : listOf<T>(raw);
  return { items, next_cursor: typeof o.next_cursor === "string" ? o.next_cursor : null };
}

/** The contacts list. The same filters and sort go with every cursor. */
export function useContactList(filters: ContactFilters, enabled = true) {
  const query = filtersToQuery(filters);
  return useInfiniteQuery({
    queryKey: queryKeys.contacts.list(query),
    queryFn: async ({ pageParam, signal }) =>
      pageOf<ContactRow>(
        await api.get<unknown>("contacts", { ...query, limit: PAGE, cursor: pageParam }, signal),
        "contacts",
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    enabled,
    staleTime: 15_000,
  });
}

/** A short search for the merge picker. */
export function useContactSearch(q: string) {
  return useQuery({
    queryKey: queryKeys.contacts.search(q),
    queryFn: async ({ signal }) =>
      pageOf<ContactRow>(await api.get<unknown>("contacts", { q, limit: 8 }, signal), "contacts")
        .items,
    enabled: q.trim().length >= 2,
    placeholderData: keepPreviousData,
  });
}

export function useContact(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.contacts.detail(id ?? ""),
    queryFn: ({ signal }) => api.get<ContactDetail>(`contacts/${id}`, undefined, signal),
    enabled: !!id,
    staleTime: 30_000,
    // Older servers do not have this route yet: the page then shows what it had before.
    retry: (n, e) => !(e instanceof ApiError && [403, 404].includes(e.status)) && n < 1,
  });
}

/** Everything that shows one customer: its row, its notes and its timeline. */
function useRefreshContact(id: string) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: queryKeys.contacts.detail(id) });
    qc.invalidateQueries({ queryKey: queryKeys.contacts.notes(id) });
    qc.invalidateQueries({ queryKey: ["contacts", id, "timeline"] });
  };
}

export function useAllNotes(id: string, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: queryKeys.contacts.notes(id),
    queryFn: async ({ pageParam, signal }) =>
      pageOf<ContactNote>(
        await api.get<unknown>(`contacts/${id}/notes`, { limit: PAGE, cursor: pageParam }, signal),
        "notes",
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled,
  });
}

export function useAddNote(id: string) {
  const refresh = useRefreshContact(id);
  return useMutation({
    mutationFn: (body: string) => api.post<ContactNote>(`contacts/${id}/notes`, { body }),
    onSuccess: refresh,
  });
}

export function useEditNote(id: string) {
  const refresh = useRefreshContact(id);
  return useMutation({
    mutationFn: (v: { noteId: string; body: string }) =>
      api
        .patch<ContactNote>(`contacts/${id}/notes/${v.noteId}`, { body: v.body })
        .then((r) => r.data),
    onSuccess: refresh,
  });
}

export function useDeleteNote(id: string) {
  const refresh = useRefreshContact(id);
  return useMutation({
    mutationFn: (noteId: string) => api.delete<void>(`contacts/${id}/notes/${noteId}`),
    onSuccess: refresh,
  });
}

/** Send only the keys that change; `null` clears one. The answer is the whole customer. */
export function useSetAttributes(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (attributes: Attributes) =>
      api.put<ContactDetail>(`contacts/${id}/attributes`, { attributes }),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.contacts.detail(id), data);
      qc.invalidateQueries({ queryKey: ["contacts", "list"] });
    },
  });
}

export function useTimeline(id: string, kinds: string[]) {
  return useInfiniteQuery({
    queryKey: queryKeys.contacts.timeline(id, kinds),
    queryFn: async ({ pageParam, signal }) =>
      pageOf<TimelineItem>(
        await api.get<unknown>(
          `contacts/${id}/timeline`,
          { kind: kinds, limit: PAGE, cursor: pageParam },
          signal,
        ),
        "items",
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

/* ---------- merge ---------- */

export function useMergePreview(survivorId: string) {
  return useMutation({
    mutationFn: (duplicateId: string) =>
      api.post<MergePreview>(`contacts/${survivorId}/merge-preview`, { duplicate_id: duplicateId }),
    meta: { silent: true },
  });
}

export function useMerge(survivorId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (duplicateId: string) =>
      api.post<{ contact: ContactRow; moved: Record<string, number> }>(
        `contacts/${survivorId}/merge`,
        { duplicate_id: duplicateId, confirm: true },
      ),
    meta: { silent: true },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.contacts.all });
      qc.invalidateQueries({ queryKey: queryKeys.tickets.all });
    },
  });
}

/* ---------- CSV ---------- */

export interface ContactImportArgs {
  file: File;
  dryRun: boolean;
  mapping?: ImportMapping;
  onExisting: OnExisting;
}

/** The CSV goes as the raw request body (not a form). Row problems come back with a 200. */
export function useImportContacts() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ file, dryRun, mapping, onExisting }: ContactImportArgs) => {
      const query: Query = { dry_run: dryRun, on_existing: onExisting };
      if (mapping) query.mapping = JSON.stringify(mapping);
      const params = new URLSearchParams();
      for (const [k, v] of Object.entries(query)) params.set(k, String(v));
      const res = await fetch(`/api/contacts/import?${params}`, {
        method: "POST",
        headers: { "Content-Type": "text/csv", Accept: "application/json" },
        credentials: "same-origin",
        body: file,
      });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        const err = ((body as { error?: { code?: string; message?: string } } | null)?.error ??
          {}) as { code?: string; message?: string };
        throw new ApiError({
          status: res.status,
          code: err.code ?? "Internal",
          message:
            res.status === 413
              ? "That file is over 2 MB. Split it and import the parts."
              : (err.message ?? "Something went wrong."),
          requestId: res.headers.get("x-request-id"),
        });
      }
      return body as ContactImportResult;
    },
    meta: { silent: true },
    onSuccess: (data) => {
      if (!data.dry_run) qc.invalidateQueries({ queryKey: queryKeys.contacts.all });
    },
  });
}

/** What the list shows (all pages): same filters and search, no cursor. */
export function exportContacts(filters: ContactFilters) {
  return downloadFile("contacts/export", "contacts.csv", filtersToQuery(filters));
}

/* ---------- contact fields ---------- */

export function useContactFields(enabled = true) {
  return useQuery({
    queryKey: queryKeys.contactFields.all,
    queryFn: () => api.get<unknown>("contact-fields"),
    select: (d) => listOf<ContactField>(d).sort((a, b) => a.position - b.position),
    enabled,
    staleTime: 60_000,
  });
}

export interface ContactFieldInput {
  key?: string;
  label?: string;
  kind?: FieldKind;
  options?: string[];
  position?: number;
}

export function useSaveContactField() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: ContactFieldInput & { id?: string }) =>
      id
        ? api.patch<ContactField>(`contact-fields/${id}`, body).then((r) => r.data)
        : api.post<ContactField>("contact-fields", body),
    meta: { silent: true },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.contactFields.all });
      qc.invalidateQueries({ queryKey: queryKeys.contacts.all });
    },
  });
}

export function useDeleteContactField() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`contact-fields/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.contactFields.all });
      qc.invalidateQueries({ queryKey: queryKeys.contacts.all });
    },
  });
}

/* ---------- segments ---------- */

export function useSegments(enabled = true) {
  return useQuery({
    queryKey: queryKeys.segments.all,
    queryFn: () => api.get<unknown>("segments"),
    select: listOf<Segment>,
    enabled,
    staleTime: 30_000,
  });
}

export function useSegment(id: string | null) {
  return useQuery({
    queryKey: queryKeys.segments.detail(id ?? ""),
    queryFn: ({ signal }) => api.get<Segment>(`segments/${id}`, undefined, signal),
    enabled: !!id,
  });
}

export function useSegmentContacts(id: string | null) {
  return useInfiniteQuery({
    queryKey: queryKeys.segments.contacts(id ?? ""),
    queryFn: async ({ pageParam, signal }) =>
      pageOf<ContactRow>(
        await api.get<unknown>(
          `segments/${id}/contacts`,
          { limit: PAGE, cursor: pageParam },
          signal,
        ),
        "contacts",
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: !!id,
  });
}

export function useSaveSegment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id?: string; name?: string; filters?: SegmentFilters }) =>
      id
        ? api.patch<Segment>(`segments/${id}`, body).then((r) => r.data)
        : api.post<Segment>("segments", body),
    meta: { silent: true },
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.segments.all }),
  });
}

export function useDeleteSegment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`segments/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.segments.all }),
  });
}

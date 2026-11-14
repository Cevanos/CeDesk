"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { TicketSummary } from "@/lib/api/types";

const DEBOUNCE_MS = 250;
export const MIN_SEARCH_CHARS = 2;
const RESULT_LIMIT = 6;

/** Returns `value` once it has stopped changing for `ms`. */
export function useDebouncedValue<T>(value: T, ms = DEBOUNCE_MS): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

/**
 * Ticket search for the command palette. Matches on the server (`?q=`) across
 * every status and every assignee, and only runs while the palette is open.
 */
export function useTicketSearch(text: string, enabled: boolean) {
  const q = text.trim();
  return useQuery({
    queryKey: [...queryKeys.tickets.all, "palette", q] as const,
    enabled: enabled && q.length >= MIN_SEARCH_CHARS,
    queryFn: async ({ signal }) => {
      const raw = await api.get<unknown>(
        "tickets",
        { q, scope: "everyone", limit: RESULT_LIMIT },
        signal,
      );
      return listOf<TicketSummary>(raw).slice(0, RESULT_LIMIT);
    },
    placeholderData: keepPreviousData,
    staleTime: 10_000,
  });
}

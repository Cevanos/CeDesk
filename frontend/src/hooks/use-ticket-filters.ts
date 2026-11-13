"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { filtersFromParams, type TicketFilters } from "@/lib/api/ticket-filters";

const SEARCH_DEBOUNCE_MS = 350;

/**
 * Ticket list filters, kept in the URL so a view can be shared and survives a
 * refresh. Search text is local while typing and reaches the URL after a pause.
 */
export function useTicketFilters() {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();

  const filters: TicketFilters = useMemo(() => filtersFromParams(sp), [sp]);

  /** Merge `patch` into the URL; a null/empty value removes the param. */
  const set = useCallback(
    (patch: Record<string, string | null>) => {
      // Read the live URL, not a render-time snapshot, so a debounced call
      // can never overwrite a filter changed while it was waiting.
      const p = new URLSearchParams(window.location.search);
      for (const [k, v] of Object.entries(patch)) {
        if (v) p.set(k, v);
        else p.delete(k);
      }
      const qs = p.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname],
  );

  const [text, setText] = useState(filters.q ?? "");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  const setSearch = useCallback(
    (value: string) => {
      setText(value);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => set({ q: value.trim() || null }), SEARCH_DEBOUNCE_MS);
    },
    [set],
  );

  const clearSearch = useCallback(() => {
    clearTimeout(timer.current);
    setText("");
    set({ q: null });
  }, [set]);

  const clearAll = useCallback(() => {
    clearTimeout(timer.current);
    setText("");
    set({ scope: null, priority: null, team: null, q: null, imported: null });
  }, [set]);

  return { filters, set, text, setSearch, clearSearch, clearAll };
}

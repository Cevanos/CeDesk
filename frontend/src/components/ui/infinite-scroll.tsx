"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

/** The slice of a TanStack infinite query this needs. */
interface PagedQuery {
  hasNextPage?: boolean;
  isFetchingNextPage: boolean;
  isError: boolean;
  fetchNextPage: () => unknown;
}

/**
 * Loads the next page when the end of a list scrolls into view. The button is
 * the fallback: after an error, or where scroll observing is unavailable.
 */
export function InfiniteScroll({
  query,
  shown,
  total,
  noun,
  className,
}: {
  query: PagedQuery;
  /** Rows loaded so far; also re-arms the observer after each page. */
  shown: number;
  /** Full count, when the API gives one. */
  total?: number;
  /** Shown next to the count, e.g. "tickets". */
  noun?: string;
  className?: string;
}) {
  const { hasNextPage: hasMore = false, isFetchingNextPage: loading, isError: failed } = query;
  const ref = useRef<HTMLDivElement>(null);
  const more = useRef(query.fetchNextPage);
  useEffect(() => {
    more.current = query.fetchNextPage;
  });

  useEffect(() => {
    const el = ref.current;
    if (!el || !hasMore || loading || failed || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) void more.current();
      },
      { rootMargin: "400px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [hasMore, loading, failed, shown]);

  if (!hasMore && !loading) {
    return shown > 0 && noun ? (
      <p className={cn("px-4 py-3 text-center text-xs text-muted-foreground", className)}>
        All {total ?? shown} {noun} loaded
      </p>
    ) : null;
  }

  return (
    <div
      ref={ref}
      className={cn(
        "flex items-center justify-center gap-3 px-4 py-3 text-xs text-muted-foreground",
        className,
      )}
    >
      <span aria-live="polite" className="inline-flex items-center gap-2">
        {loading && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
        {loading ? "Loading more…" : `Showing ${shown}${total != null ? ` of ${total}` : ""}`}
        {!loading && noun ? ` ${noun}` : ""}
      </span>
      {!loading && failed && (
        <button
          type="button"
          onClick={() => void query.fetchNextPage()}
          className="rounded-lg border border-border px-2.5 py-1 font-medium text-foreground transition-colors hover:bg-surface-2"
        >
          Try again
        </button>
      )}
    </div>
  );
}

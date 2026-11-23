"use client";

import { Archive, Search } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Badge, Button, Card, EmptyState, Input, Skeleton } from "@/components/ui/primitives";
import {
  ARCHIVE_QUERY_HINT,
  parseArchiveQuery,
  useArchiveSearch,
  type ArchivedTicketRow,
  type ArchiveSearch,
} from "@/hooks/use-archive";
import { ApiError } from "@/lib/api/client";
import { formatDateTime } from "@/lib/utils";

export function ticketName(t: ArchivedTicketRow): string {
  if (t.label) return t.label;
  if (t.number != null && t.number !== "") return `T-${t.number}`;
  return t.id.slice(0, 8);
}

export function ArchiveSearchView({ initial }: { initial?: ArchiveSearch | null } = {}) {
  const params = useSearchParams();
  const start = params.get("q") ?? "";
  const [text, setText] = useState(start);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => ref.current?.focus(), []);
  const [filters, setFilters] = useState<ArchiveSearch | null>(initial ?? parseArchiveQuery(start));
  const [invalid, setInvalid] = useState(() => !initial && !!start.trim() && !filters);
  const q = useArchiveSearch(filters);
  const rows = q.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="mx-auto w-full max-w-4xl px-3 py-5 sm:px-6 sm:py-8">
      <h1 className="text-2xl font-semibold tracking-tight">Archive</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Old resolved tickets live here. Find one by its number or by the customer, then open it to
        read it. Archived tickets can&apos;t be replied to, changed or reopened.
      </p>

      <form
        role="search"
        className="mt-5 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const f = parseArchiveQuery(text);
          setFilters(f);
          setInvalid(!f && !!text.trim());
        }}
      >
        <div className="relative flex-1">
          <Search
            className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            ref={ref}
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-label="Search the archive"
            placeholder="Ticket number (1042 or T-1042), customer email or customer id"
            className="pl-9"
          />
        </div>
        <Button type="submit" disabled={!text.trim()}>
          Search
        </Button>
      </form>

      <div className="mt-5">
        {!filters ? (
          <Card>
            <EmptyState icon={<Archive className="size-5" />} title="Search the archive">
              {invalid
                ? ARCHIVE_QUERY_HINT
                : "Type a ticket number like 1042, or a customer's email."}
            </EmptyState>
          </Card>
        ) : q.isLoading ? (
          <div className="space-y-2" aria-busy="true">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-16 rounded-xl" />
            ))}
          </div>
        ) : q.isError ? (
          <Card className="p-5 text-sm text-danger" role="alert">
            {q.error instanceof ApiError
              ? q.error.status === 422
                ? q.error.message || q.error.userMessage
                : q.error.userMessage
              : "Couldn't search the archive."}{" "}
            <button className="font-medium underline" onClick={() => q.refetch()}>
              Try again
            </button>
          </Card>
        ) : rows.length === 0 ? (
          <Card>
            <EmptyState icon={<Search className="size-5" />} title="Nothing found">
              No archived ticket matches that. Check the number or email.
            </EmptyState>
          </Card>
        ) : (
          <Card className="overflow-hidden">
            <ul className="divide-y divide-border">
              {rows.map((t) => (
                <li key={t.id}>
                  <Link
                    href={`/archive/${encodeURIComponent(t.id)}`}
                    className="flex items-center gap-3 px-4 py-3.5 transition-colors hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none"
                  >
                    <span className="w-20 flex-none font-mono text-xs text-muted-foreground">
                      {ticketName(t)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {t.subject || "(no subject)"}
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {t.customer_name ?? t.contact_name ?? ""}
                        {(t.customer_email ?? t.contact_email)
                          ? ` · ${t.customer_email ?? t.contact_email}`
                          : ""}
                      </span>
                    </span>
                    <Badge>Archived</Badge>
                    <time
                      className="hidden w-28 flex-none text-right text-xs text-muted-foreground sm:block"
                      dateTime={t.resolved_at ?? t.updated_at ?? undefined}
                    >
                      {formatDateTime(t.resolved_at ?? t.updated_at)}
                    </time>
                  </Link>
                </li>
              ))}
            </ul>
            {q.hasNextPage && (
              <div className="border-t border-border p-3 text-center">
                <Button
                  variant="ghost"
                  onClick={() => q.fetchNextPage()}
                  loading={q.isFetchingNextPage}
                >
                  Show more
                </Button>
              </div>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}

"use client";

import { Inbox, Loader2, Plus } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { BulkBar } from "@/components/tickets/inbox/bulk-bar";
import { SavedViews } from "@/components/tickets/inbox/saved-views";
import { FilterBar, SORTS, activeChips } from "@/components/tickets/list/filter-bar";
import { StatusTabs } from "@/components/tickets/list/status-tabs";
import { VirtualTicketList } from "@/components/tickets/list/virtual-ticket-list";
import { Button, Card, EmptyState, Skeleton } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { useTicketFilters } from "@/hooks/use-ticket-filters";
import { useTickets } from "@/hooks/use-tickets";
import { useTeams } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import { inboxActions } from "@/lib/permissions";

function ListSkeleton() {
  return (
    <ul className="divide-y divide-border">
      {Array.from({ length: 7 }).map((_, i) => (
        <li key={i} className="flex items-center gap-3 px-4 py-4">
          <Skeleton className="size-9 flex-none rounded-full" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-3 w-1/3" />
          </div>
          <Skeleton className="hidden h-3 w-12 sm:block" />
        </li>
      ))}
    </ul>
  );
}

export function TicketListView() {
  const { user, perms } = useCurrentUser();
  const canBulk = inboxActions(user).bulkEdit;
  const teams = useTeams();
  const { filters, set, text, setSearch, clearSearch, clearAll, applyView } = useTicketFilters();

  const q = useTickets(filters);
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data]);
  const counts = q.data?.pages[0]?.counts ?? {};

  const teamName = teams.data?.find((t) => t.id === filters.team)?.name;
  const chips = activeChips(filters, teamName);
  // The selection belongs to one list view: it is dropped when the filters change.
  const listKey = JSON.stringify(filters);
  const [picked, setPicked] = useState<{ key: string; ids: ReadonlySet<string> }>({
    key: listKey,
    ids: new Set(),
  });
  // Only tickets still on screen count (a refresh may have moved some out of this view).
  const selected = useMemo(
    () =>
      new Set(
        picked.key === listKey ? rows.filter((r) => picked.ids.has(r.id)).map((r) => r.id) : [],
      ),
    [picked, listKey, rows],
  );
  const setSelected = (ids: Iterable<string>) => setPicked({ key: listKey, ids: new Set(ids) });
  const toggle = (id: string) => {
    const next = new Set(selected);
    if (!next.delete(id)) next.add(id);
    setSelected(next);
  };
  const labels = useMemo(() => Object.fromEntries(rows.map((r) => [r.id, r.label])), [rows]);
  const allPicked = rows.length > 0 && selected.size === rows.length;

  const sortLabel = SORTS.find(([v]) => v === filters.sort)?.[1].toLowerCase();

  return (
    <div className="mx-auto w-full max-w-6xl px-3 py-5 sm:px-6 sm:py-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-[26px] leading-tight font-semibold tracking-tight sm:text-3xl">
            Tickets
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Every conversation, human and AI, in one queue.
          </p>
        </div>
        {perms.createTicket && (
          <Link href="/tickets/new" className="lg:hidden">
            <Button size="sm">
              <Plus className="size-4" /> New
            </Button>
          </Link>
        )}
      </div>

      <StatusTabs
        active={filters.snoozed === "true" ? "snoozed" : filters.status}
        counts={counts}
        loading={q.isPending}
        onChange={(s) =>
          s === "snoozed"
            ? // Snoozed tickets are Pending until they wake up.
              set({ status: "pending", snoozed: "true" })
            : set({ status: s === "open" ? null : s, snoozed: null })
        }
      />

      <SavedViews filters={filters} onApply={applyView} />

      <FilterBar
        filters={filters}
        teams={teams.data}
        chips={chips}
        text={text}
        onSearch={setSearch}
        onClearSearch={clearSearch}
        onClearAll={clearAll}
        set={set}
      />

      {canBulk && selected.size > 0 && (
        <BulkBar
          ids={[...selected]}
          labels={labels}
          onClear={() => setSelected([])}
          onDone={(failed) => setSelected(failed)}
        />
      )}

      <div className="mt-4 mb-1 flex items-center justify-between gap-3 px-1 text-xs text-muted-foreground">
        <span className="flex items-center gap-3">
          {canBulk && rows.length > 0 && (
            <label className="inline-flex cursor-pointer items-center gap-1.5">
              <input
                type="checkbox"
                checked={allPicked}
                ref={(el) => {
                  if (el) el.indeterminate = selected.size > 0 && !allPicked;
                }}
                onChange={() => setSelected(allPicked ? [] : rows.map((r) => r.id))}
                className="size-4 accent-[var(--primary)]"
              />
              <span className="sr-only sm:not-sr-only">Select all</span>
            </label>
          )}
          <span aria-live="polite">
            {q.isPending
              ? "Loading tickets…"
              : `${rows.length}${q.hasNextPage ? "+" : ""} ${rows.length === 1 ? "ticket" : "tickets"}`}
          </span>
          {filters.snoozed !== "true" && (
            <label className="inline-flex cursor-pointer items-center gap-1.5">
              <input
                type="checkbox"
                checked={filters.snoozed === "false"}
                onChange={(e) => set({ snoozed: e.target.checked ? "false" : null })}
                className="size-3.5 accent-[var(--primary)]"
              />
              Hide snoozed
            </label>
          )}
        </span>
        <span className="hidden sm:inline">Sorted by {sortLabel}</span>
      </div>

      <Card className="overflow-hidden">
        {q.isPending ? (
          <ListSkeleton />
        ) : q.isError ? (
          <EmptyState title="We couldn't load tickets">
            {q.error instanceof ApiError ? q.error.userMessage : "Please try again."}
            <span className="mt-3 block">
              <Button variant="outline" size="sm" onClick={() => q.refetch()}>
                Retry
              </Button>
            </span>
          </EmptyState>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<Inbox className="size-6" />}
            title={chips.length ? "No tickets match" : "Inbox zero"}
          >
            {chips.length
              ? "Nothing matches these filters. Try another tab or clear the filters."
              : "No tickets in this tab right now."}
            {chips.length > 0 && (
              <span className="mt-3 block">
                <Button variant="outline" size="sm" onClick={clearAll}>
                  Clear filters
                </Button>
              </span>
            )}
          </EmptyState>
        ) : (
          <VirtualTicketList
            rows={rows}
            activeStatus={filters.status}
            selectable={canBulk}
            selected={selected}
            onToggle={toggle}
          />
        )}
        {q.hasNextPage && (
          <div className="border-t border-border bg-surface-2/40 p-3 text-center">
            <Button
              variant="outline"
              size="sm"
              onClick={() => q.fetchNextPage()}
              disabled={q.isFetchingNextPage}
            >
              {q.isFetchingNextPage && <Loader2 className="size-4 animate-spin" />} Load more
              tickets
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}

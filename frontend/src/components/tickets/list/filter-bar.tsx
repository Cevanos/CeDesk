"use client";

import { Search, SlidersHorizontal, X } from "lucide-react";
import { useState } from "react";

import { PRIORITIES, PRIORITY_LABEL } from "@/components/tickets/badges";
import { Button, Card, Input, Select } from "@/components/ui/primitives";
import { IMPORT_SOURCES, type TicketFilters } from "@/lib/api/ticket-filters";
import type { Priority } from "@/lib/api/types";

export const SORTS = [
  ["priority", "Priority"],
  ["newest", "Newest"],
  ["oldest", "Oldest"],
  ["updated", "Recently updated"],
  ["sla", "Time left"],
] as const;
export const SCOPES = [
  ["everyone", "Everyone"],
  ["mine", "Mine"],
  ["unassigned", "Unassigned"],
] as const;

type SetFilters = (patch: Record<string, string | null>) => void;

interface Chip {
  key: string;
  label: string;
  clear: Record<string, string | null>;
}

export function activeChips(filters: TicketFilters, teamName?: string): Chip[] {
  const chips: Chip[] = [];
  if (filters.scope && filters.scope !== "everyone") {
    chips.push({
      key: "scope",
      label: SCOPES.find(([v]) => v === filters.scope)?.[1] ?? filters.scope,
      clear: { scope: null },
    });
  }
  if (filters.priority) {
    chips.push({
      key: "priority",
      label: `${PRIORITY_LABEL[filters.priority as Priority]} priority`,
      clear: { priority: null },
    });
  }
  if (filters.imported) {
    chips.push({
      key: "imported",
      label:
        filters.imported === "any"
          ? "Imported"
          : `Imported from ${IMPORT_SOURCES.find(([v]) => v === filters.imported)?.[1] ?? filters.imported}`,
      clear: { imported: null },
    });
  }
  if (filters.team) chips.push({ key: "team", label: teamName ?? "Team", clear: { team: null } });
  if (filters.q) chips.push({ key: "q", label: `“${filters.q}”`, clear: { q: null } });
  return chips;
}

export function FilterBar({
  filters,
  teams,
  chips,
  text,
  onSearch,
  onClearSearch,
  onClearAll,
  set,
}: {
  filters: TicketFilters;
  teams?: { id: string; name: string }[];
  chips: Chip[];
  text: string;
  onSearch: (v: string) => void;
  onClearSearch: () => void;
  onClearAll: () => void;
  set: SetFilters;
}) {
  const [showFilters, setShowFilters] = useState(false);

  return (
    <Card className="mt-4 p-2.5 sm:p-3">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search
            className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-subtle-foreground"
            aria-hidden
          />
          <Input
            value={text}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search T-1005, subject, customer, tag…"
            aria-label="Search tickets"
            className="border-transparent bg-surface-2 pr-9 pl-9 hover:border-border-strong focus:bg-surface"
            type="search"
          />
          {text && (
            <button
              onClick={onClearSearch}
              aria-label="Clear search"
              className="absolute top-1/2 right-2 grid size-6 -translate-y-1/2 place-items-center rounded-md text-subtle-foreground hover:bg-surface-3 hover:text-foreground"
            >
              <X className="size-3.5" aria-hidden />
            </button>
          )}
        </div>

        <Button
          variant="outline"
          className="relative lg:hidden"
          onClick={() => setShowFilters((v) => !v)}
          aria-expanded={showFilters}
          aria-controls="ticket-filters"
        >
          <SlidersHorizontal className="size-4" aria-hidden />
          <span className="hidden sm:inline">Filters</span>
          {chips.length > 0 && (
            <span className="absolute -top-1.5 -right-1.5 grid size-[18px] place-items-center rounded-full bg-primary text-[10px] font-semibold text-primary-foreground">
              {chips.length}
            </span>
          )}
        </Button>

        <div className="hidden items-center gap-2 lg:flex">
          <FilterSelects filters={filters} set={set} teams={teams} />
        </div>
      </div>

      {showFilters && (
        <div id="ticket-filters" className="mt-2.5 grid gap-2 sm:grid-cols-2 lg:hidden">
          <FilterSelects filters={filters} set={set} teams={teams} stacked />
        </div>
      )}

      {chips.length > 0 && (
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-border pt-2.5">
          <span className="mr-0.5 text-xs text-muted-foreground">Filtered by</span>
          {chips.map((c) => (
            <button
              key={c.key}
              onClick={() => (c.key === "q" ? onClearSearch() : set(c.clear))}
              className="group inline-flex max-w-[16rem] items-center gap-1 rounded-full bg-primary-soft py-0.5 pr-1.5 pl-2.5 text-xs font-medium text-primary-ink transition-colors hover:bg-primary/20"
              aria-label={`Remove filter ${c.label}`}
            >
              <span className="truncate">{c.label}</span>
              <X className="size-3 flex-none opacity-70 group-hover:opacity-100" aria-hidden />
            </button>
          ))}
          <button
            onClick={onClearAll}
            className="ml-1 text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            Clear all
          </button>
        </div>
      )}
    </Card>
  );
}

function FilterSelects({
  filters,
  set,
  teams,
  stacked,
}: {
  filters: TicketFilters;
  set: SetFilters;
  teams?: { id: string; name: string }[];
  stacked?: boolean;
}) {
  const cls = stacked ? "" : "w-auto min-w-[8.5rem]";
  return (
    <>
      <Select
        aria-label="Scope"
        className={cls}
        value={filters.scope}
        onChange={(e) => set({ scope: e.target.value === "everyone" ? null : e.target.value })}
      >
        {SCOPES.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </Select>
      <Select
        aria-label="Priority"
        className={cls}
        value={filters.priority}
        onChange={(e) => set({ priority: e.target.value || null })}
      >
        <option value="">All priorities</option>
        {PRIORITIES.map((p) => (
          <option key={p} value={p}>
            {PRIORITY_LABEL[p]}
          </option>
        ))}
      </Select>
      <Select
        aria-label="Team"
        className={cls}
        value={filters.team}
        onChange={(e) => set({ team: e.target.value || null })}
      >
        <option value="">All teams</option>
        {teams?.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </Select>
      <Select
        aria-label="Imported"
        className={cls}
        value={filters.imported ?? ""}
        onChange={(e) => set({ imported: e.target.value || null })}
      >
        <option value="">All tickets</option>
        <option value="any">Imported (any source)</option>
        {IMPORT_SOURCES.map(([v, l]) => (
          <option key={v} value={v}>
            Imported from {l}
          </option>
        ))}
      </Select>
      <Select
        aria-label="Sort"
        className={cls}
        value={filters.sort}
        onChange={(e) => set({ sort: e.target.value === "priority" ? null : e.target.value })}
      >
        {SORTS.map(([v, l]) => (
          <option key={v} value={v}>
            Sort: {l}
          </option>
        ))}
      </Select>
    </>
  );
}

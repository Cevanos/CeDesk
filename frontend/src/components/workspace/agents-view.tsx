"use client";

import { ChevronRight, RefreshCw, Search, UserSearch, UsersRound, X } from "lucide-react";
import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { STATUS_META } from "@/components/layout/user-menu";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Skeleton,
} from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { useAgents, useRoutingDefaults, useUpdateRoutingDefaults } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { AgentRow, AgentStatus } from "@/lib/api/types";
import { ROLE_LABEL } from "@/lib/permissions";
import { cn } from "@/lib/utils";

import { Avatar, CHANNELS, LoadBar, totalCapacity } from "./agent-shared";

// The drawer (routing editor, forms) is only needed once a row is opened.
const AgentDrawer = dynamic(() => import("./agent-drawer").then((m) => m.AgentDrawer));

type Filter = "all" | AgentStatus;

export function AgentsView() {
  const { perms } = useCurrentUser();
  const agents = useAgents();
  const defaults = useRoutingDefaults();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const list = useMemo(() => agents.data ?? [], [agents.data]);
  // Always read the selected agent from live data so edits and polling show up in the drawer.
  const selected = list.find((a) => a.member_id === selectedId) ?? null;

  const counts = useMemo(() => {
    const c: Record<Filter, number> = {
      all: list.length,
      available: 0,
      on_break: 0,
      transfers_only: 0,
      offline: 0,
    };
    // A light agent has no status, so they count only under "all".
    for (const a of list) if (a.status) c[a.status] = (c[a.status] ?? 0) + 1;
    return c;
  }, [list]);
  const totalOpen = useMemo(() => list.reduce((n, a) => n + a.open_tickets, 0), [list]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    return list.filter((a) => {
      if (filter !== "all" && a.status !== filter) return false;
      if (!q) return true;
      const hay = [
        a.name,
        ROLE_LABEL[a.role] ?? a.role,
        a.status ? STATUS_META[a.status]?.label : "",
        ...a.skills,
        ...a.channels,
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [list, query, filter]);

  return (
    <div className="mx-auto w-full max-w-6xl px-3 py-5 sm:px-6 sm:py-8">
      <header className="relative flex flex-wrap items-end justify-between gap-3 overflow-hidden rounded-2xl border border-border bg-surface p-5 shadow-card sm:p-6">
        <span
          aria-hidden
          className="pointer-events-none absolute -top-24 -right-16 size-60 rounded-full bg-ai-gradient opacity-15 blur-3xl"
        />
        <div className="relative flex items-center gap-4">
          <span className="grid size-12 flex-none place-items-center rounded-2xl bg-ai-gradient text-white shadow-[0_8px_22px_-8px_var(--ai-from)]">
            <UsersRound className="size-6" aria-hidden />
          </span>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
            <p className="text-sm text-muted-foreground">
              Who is online, how busy they are and what they handle. Refreshes every 30 seconds.
            </p>
          </div>
        </div>
        <Button
          className="relative"
          variant="outline"
          size="sm"
          onClick={() => agents.refetch()}
          disabled={agents.isFetching}
          aria-label="Refresh agents"
        >
          <RefreshCw className={cn("size-3.5", agents.isFetching && "animate-spin")} /> Refresh
        </Button>
      </header>

      <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          { label: "Total agents", value: counts.all, dot: "bg-primary" },
          { label: "Available now", value: counts.available, dot: "bg-success" },
          {
            label: "Away or offline",
            value: counts.on_break + counts.transfers_only + counts.offline,
            dot: "bg-subtle-foreground",
          },
          { label: "Open tickets", value: totalOpen, dot: "bg-info" },
        ].map((s) => (
          <Card key={s.label} className="relative overflow-hidden p-4">
            <div aria-hidden className={cn("absolute top-0 left-0 h-full w-1", s.dot)} />
            <div className="text-xs font-medium text-muted-foreground">{s.label}</div>
            <div className="mt-1 text-3xl font-semibold tracking-tight tabular-nums">
              {agents.isPending ? <Skeleton className="h-8 w-12" /> : s.value}
            </div>
          </Card>
        ))}
      </div>

      <div className="mt-6 flex flex-col gap-3 md:flex-row md:items-center">
        <div className="relative md:max-w-md md:flex-1">
          <Search
            className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-subtle-foreground"
            aria-hidden
          />
          <Input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search agents by name, skill, channel or role…"
            aria-label="Search agents"
            className="h-11 rounded-xl pr-10 pl-10 shadow-card"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label="Clear search"
              className="absolute top-1/2 right-2.5 grid size-6 -translate-y-1/2 place-items-center rounded-md text-muted-foreground hover:bg-surface-2"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
        <div
          className="-mx-3 flex gap-1.5 overflow-x-auto px-3 pb-1 md:mx-0 md:px-0 md:pb-0"
          role="tablist"
          aria-label="Filter by status"
        >
          {(["all", "available", "on_break", "transfers_only", "offline"] as Filter[]).map((k) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={filter === k}
              onClick={() => setFilter(k)}
              className={cn(
                "inline-flex flex-none items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] font-medium transition-all",
                filter === k
                  ? "border-primary bg-primary text-primary-foreground shadow-[0_6px_16px_-8px_var(--primary)]"
                  : "border-border bg-surface text-foreground-2 hover:border-border-strong hover:bg-surface-2",
              )}
            >
              {k !== "all" && (
                <span
                  className={cn(
                    "size-2 rounded-full",
                    filter === k ? "bg-primary-foreground" : STATUS_META[k].dot,
                  )}
                />
              )}
              {k === "all" ? "All" : STATUS_META[k].label}
              <span
                className={cn(
                  "text-xs tabular-nums",
                  filter === k ? "opacity-80" : "text-subtle-foreground",
                )}
              >
                {counts[k]}
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {agents.isPending ? (
          Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-44 rounded-2xl" />)
        ) : agents.isError ? (
          <div className="sm:col-span-2 xl:col-span-3">
            <EmptyState title="We couldn't load agents">
              {agents.error instanceof ApiError ? agents.error.userMessage : ""}
            </EmptyState>
          </div>
        ) : results.length === 0 ? (
          <div className="sm:col-span-2 xl:col-span-3">
            <EmptyState
              icon={<UserSearch className="size-6" />}
              title={list.length ? "No agents match your search" : "No agents yet"}
            >
              {list.length
                ? "Try a different name, skill or status filter."
                : "Agents appear here once members are added to the workspace."}
            </EmptyState>
            {(query || filter !== "all") && (
              <div className="grid place-items-center">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setQuery("");
                    setFilter("all");
                  }}
                >
                  Clear filters
                </Button>
              </div>
            )}
          </div>
        ) : (
          results.map((a, i) => (
            <AgentCard
              key={a.member_id}
              agent={a}
              cap={totalCapacity(a, defaults.data)}
              index={i}
              onOpen={() => setSelectedId(a.member_id)}
            />
          ))
        )}
      </div>

      {perms.manageSettings && <RoutingDefaults />}

      <AgentDrawer
        agent={selected}
        canEdit={perms.manageSettings}
        onClose={() => setSelectedId(null)}
      />
    </div>
  );
}

function AgentCard({
  agent: a,
  cap,
  index,
  onOpen,
}: {
  agent: AgentRow;
  cap: number | null;
  index: number;
  onOpen: () => void;
}) {
  const s = a.status ? (STATUS_META[a.status] ?? STATUS_META.offline) : null;
  const shownSkills = a.skills.slice(0, 3);
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`View ${a.name}`}
      style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}
      className="group relative animate-rise overflow-hidden rounded-2xl border border-border bg-surface p-4 text-left shadow-card transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-pop focus-visible:-translate-y-0.5"
    >
      <div
        aria-hidden
        className="pointer-events-none absolute -top-12 -right-12 size-32 rounded-full bg-primary/10 opacity-0 blur-2xl transition-opacity duration-300 group-hover:opacity-100"
      />
      <div className="relative flex items-center gap-3">
        <Avatar name={a.name} status={a.status} />
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold">{a.name}</div>
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span>{ROLE_LABEL[a.role] ?? a.role}</span>
            {s && (
              <>
                <span aria-hidden>·</span>
                <span className="inline-flex items-center gap-1">
                  <span className={cn("size-1.5 rounded-full", s.dot)} /> {s.label}
                </span>
              </>
            )}
          </div>
        </div>
        <ChevronRight
          className="size-4 flex-none text-subtle-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary-ink"
          aria-hidden
        />
      </div>

      <div className="relative mt-4">
        <div className="mb-1.5 flex items-baseline justify-between text-xs">
          <span className="text-muted-foreground">Workload</span>
          <span className="font-medium tabular-nums">
            {a.open_tickets}
            {cap ? (
              <span className="text-muted-foreground"> / {cap}</span>
            ) : (
              <span className="text-muted-foreground"> open</span>
            )}
          </span>
        </div>
        {cap ? (
          <LoadBar open={a.open_tickets} cap={cap} />
        ) : (
          <div className="h-1.5 rounded-full bg-surface-3" />
        )}
      </div>

      <div className="relative mt-4 flex flex-wrap items-center gap-1.5">
        {a.channels.map((c) => {
          const meta = CHANNELS.find((x) => x.key === c);
          const Icon = meta?.icon;
          return (
            <Badge key={c} tone="info">
              {Icon && <Icon className="size-3" />} {meta?.label ?? c}
              {a.open_by_channel?.[c] !== undefined && (
                <span className="tabular-nums" title="Open tickets on this channel">
                  : {a.open_by_channel[c]}
                </span>
              )}
            </Badge>
          );
        })}
        {shownSkills.map((k) => (
          <Badge key={k} tone="primary">
            {k}
          </Badge>
        ))}
        {a.skills.length > shownSkills.length && (
          <Badge>+{a.skills.length - shownSkills.length}</Badge>
        )}
        {!a.channels.length && !a.skills.length && (
          <span className="text-xs text-subtle-foreground">No channels or skills set</span>
        )}
      </div>
    </button>
  );
}

function RoutingDefaults() {
  const q = useRoutingDefaults();
  const save = useUpdateRoutingDefaults();
  const [draft, setDraft] = useState<Record<string, string>>({});

  if (!q.data) return null;
  const items = [
    ["cap_email", "Email"],
    ["cap_chat", "Chat"],
    ["cap_messaging", "Messaging"],
  ] as const;

  return (
    <Card className="mt-8 p-4 sm:p-5">
      <h2 className="font-semibold">Default capacity per channel</h2>
      <p className="mb-4 text-sm text-muted-foreground">
        How many open tickets an agent can hold before auto-assignment skips them.
      </p>
      <form
        className="grid gap-4 sm:grid-cols-4 sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          const body = Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, Number(v)]));
          save.mutate(body, {
            onSuccess: () => {
              toast.success("Defaults saved");
              setDraft({});
            },
          });
        }}
      >
        {items.map(([k, label]) => (
          <Field key={k} label={label} htmlFor={k}>
            <Input
              id={k}
              type="number"
              min={0}
              max={200}
              value={draft[k] ?? String(q.data[k] ?? "")}
              onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))}
            />
          </Field>
        ))}
        <Button type="submit" loading={save.isPending} disabled={!Object.keys(draft).length}>
          Save
        </Button>
      </form>
    </Card>
  );
}

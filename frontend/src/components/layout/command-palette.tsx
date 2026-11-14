"use client";

import { Command as CommandIcon, CornerDownLeft, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useCurrentUser } from "@/hooks/use-auth";
import { useFocusTrap } from "@/hooks/use-focus-trap";
import { MIN_SEARCH_CHARS, useDebouncedValue, useTicketSearch } from "@/hooks/use-global-search";
import { useAgents } from "@/hooks/workspace/agents";
import { cn } from "@/lib/utils";

interface Cmd {
  id: string;
  group: "navigate" | "actions" | "tickets" | "agents";
  label: string;
  /** Secondary text shown under the label (ticket number, contact, etc). */
  hint?: string;
  run: () => void;
}

/**
 * Cmd/Ctrl+K launcher. Opened by the shortcut or by dispatching the
 * `cdesk:palette` window event (the header button does this).
 */
export function CommandPalette() {
  const t = useTranslations("palette");
  const router = useRouter();
  const { perms } = useCurrentUser();
  const { resolvedTheme, setTheme } = useTheme();

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  const panelRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  useFocusTrap(panelRef, open);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setActive(0);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("cdesk:palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("cdesk:palette", onOpen);
    };
  }, []);

  const commands = useMemo<Cmd[]>(() => {
    const go = (href: string) => () => router.push(href);
    const list: Cmd[] = [
      { id: "tickets", group: "navigate", label: t("goTickets"), run: go("/tickets") },
      { id: "agents", group: "navigate", label: t("goAgents"), run: go("/agents") },
    ];
    if (perms.createTicket) {
      list.push({ id: "new", group: "actions", label: t("goNewTicket"), run: go("/tickets/new") });
    }
    if (perms.manageSettings) {
      list.push(
        { id: "members", group: "navigate", label: t("goMembers"), run: go("/settings/members") },
        { id: "teams", group: "navigate", label: t("goTeams"), run: go("/settings/teams") },
        {
          id: "types",
          group: "navigate",
          label: t("goTicketTypes"),
          run: go("/settings/ticket-types"),
        },
        { id: "sla", group: "navigate", label: t("goSla"), run: go("/settings/sla") },
        {
          id: "saved-replies",
          group: "navigate",
          label: t("goSavedReplies"),
          run: go("/settings/saved-replies"),
        },
        { id: "macros", group: "navigate", label: t("goMacros"), run: go("/settings/macros") },
        { id: "rules", group: "navigate", label: t("goRules"), run: go("/settings/rules") },
        { id: "email", group: "navigate", label: t("goEmail"), run: go("/settings/email") },
        { id: "cevanos", group: "navigate", label: t("goCevanos"), run: go("/settings/cevanos") },
        { id: "failed", group: "navigate", label: t("goFailed"), run: go("/settings/failed") },
      );
    }
    list.push({
      id: "theme",
      group: "actions",
      label: t("theme"),
      run: () => setTheme(resolvedTheme === "dark" ? "light" : "dark"),
    });
    return list;
  }, [perms.createTicket, perms.manageSettings, resolvedTheme, router, setTheme, t]);

  const debounced = useDebouncedValue(query);
  const needle = debounced.trim().toLowerCase();
  const searching = open && needle.length >= MIN_SEARCH_CHARS;
  const ticketSearch = useTicketSearch(debounced, open);
  const agentList = useAgents({ enabled: searching });

  const results = useMemo<Cmd[]>(() => {
    const q = query.trim().toLowerCase();
    const matched = q ? commands.filter((c) => c.label.toLowerCase().includes(q)) : commands;
    if (!searching) return matched;

    const agents: Cmd[] = (agentList.data ?? [])
      .filter((a) => a.name.toLowerCase().includes(needle))
      .slice(0, 5)
      .map((a) => ({
        id: `agent-${a.member_id}`,
        group: "agents",
        label: a.name,
        hint: t("agentOpen", { count: a.open_tickets }),
        run: () => router.push("/agents"),
      }));
    const tickets: Cmd[] = (ticketSearch.data ?? []).map((tk) => ({
      id: `ticket-${tk.id}`,
      group: "tickets",
      label: tk.subject,
      hint: [tk.label, tk.contact?.name].filter(Boolean).join(" · "),
      run: () => router.push(`/tickets/${tk.id}`),
    }));
    return [...tickets, ...agents, ...matched];
  }, [agentList.data, commands, needle, query, router, searching, t, ticketSearch.data]);

  const choose = (c: Cmd | undefined) => {
    if (!c) return;
    close();
    c.run();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (results.length ? (i + 1) % results.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (results.length ? (i - 1 + results.length) % results.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(results[Math.min(active, results.length - 1)]);
    }
  };

  if (!open || typeof document === "undefined") return null;

  const activeIndex = Math.min(active, Math.max(results.length - 1, 0));

  return createPortal(
    <div
      className="fixed inset-0 z-[70] grid place-items-start bg-[#0b1226]/55 p-4 pt-[12svh] backdrop-blur-sm"
      role="presentation"
      onMouseDown={(e) => e.target === e.currentTarget && close()}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={t("open")}
        className="mx-auto w-full max-w-lg animate-rise overflow-hidden rounded-2xl border border-border bg-surface shadow-hero outline-none"
      >
        <div className="flex items-center gap-2 border-b border-border px-3.5">
          <Search className="size-4 flex-none text-subtle-foreground" aria-hidden />
          <input
            ref={(el) => el?.focus()}
            onKeyDown={onKeyDown}
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={results[activeIndex] ? `${listId}-${activeIndex}` : undefined}
            aria-label={t("placeholder")}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            placeholder={t("placeholder")}
            className="h-12 w-full bg-transparent text-sm outline-none placeholder:text-subtle-foreground"
          />
        </div>
        <ul id={listId} role="listbox" className="max-h-80 overflow-y-auto p-1.5">
          {results.length === 0 && (
            <li className="px-3 py-8 text-center text-sm text-muted-foreground">
              {ticketSearch.isFetching || agentList.isFetching ? t("searching") : t("empty")}
            </li>
          )}
          {results.map((c, i) => (
            // Keyboard use goes through the combobox input (aria-activedescendant).
            // eslint-disable-next-line jsx-a11y/click-events-have-key-events
            <li
              key={c.id}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === activeIndex}
              onMouseMove={() => setActive(i)}
              onClick={() => choose(c)}
              className={cn(
                "flex cursor-pointer items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-sm",
                i === activeIndex ? "bg-primary-soft text-primary-ink" : "text-foreground-2",
              )}
            >
              <span className="min-w-0">
                <span className="block truncate">{c.label}</span>
                {c.hint && (
                  <span className="block truncate text-xs text-muted-foreground">{c.hint}</span>
                )}
              </span>
              <span className="flex-none text-[11px] tracking-wide text-subtle-foreground uppercase">
                {t(c.group)}
              </span>
            </li>
          ))}
        </ul>
        <div className="flex items-center justify-end gap-3 border-t border-border px-3.5 py-2 text-[11px] text-subtle-foreground">
          <span className="inline-flex items-center gap-1">
            <CornerDownLeft className="size-3" aria-hidden /> Enter
          </span>
          <span className="inline-flex items-center gap-1">
            <CommandIcon className="size-3" aria-hidden /> K
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}

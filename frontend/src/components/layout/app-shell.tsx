"use client";

import {
  Archive,
  BarChart3,
  Contact,
  Inbox,
  Megaphone,
  Menu,
  Plus,
  Search,
  Settings,
  UsersRound,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { OfflineBanner } from "@/components/feedback/offline-banner";
import { CommandPalette } from "@/components/layout/command-palette";
import { NotificationBell } from "@/components/layout/notification-bell";
import { ResourcesMenu } from "@/components/layout/resources-menu";
import { UserMenu } from "@/components/layout/user-menu";
import { WorkMenu } from "@/components/layout/work-menu";
import { Logo } from "@/components/ui/brand";
import { Button } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { useEscape } from "@/hooks/use-escape";
import { useFocusTrap } from "@/hooks/use-focus-trap";
import { useAgentLiveUpdates } from "@/hooks/use-live-stream";
import { ROLE_LABEL, canManageCampaigns, hasAction } from "@/lib/permissions";
import { cn, initials } from "@/lib/utils";

const NAV = [
  { href: "/tickets", label: "Tickets", icon: Inbox },
  { href: "/contacts", label: "Contacts", icon: Contact },
  { href: "/agents", label: "Agents", icon: UsersRound },
  { href: "/archive", label: "Archive", icon: Archive },
  { href: "/campaigns", label: "Campaigns", icon: Megaphone, campaigns: true },
  { href: "/reports/team", label: "Reports", icon: BarChart3, reports: true, match: "/reports" },
  { href: "/settings/members", label: "Settings", icon: Settings, admin: true, match: "/settings" },
] as const;

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, perms } = useCurrentUser();
  // A workspace that requires 2FA sends people without it to set it up first.
  const mustSetUp2fa = user?.two_factor_setup_required === true;
  useEffect(() => {
    if (mustSetUp2fa && !pathname.startsWith("/account")) router.replace("/account?setup=2fa");
  }, [mustSetUp2fa, pathname, router]);
  const t = useTranslations("palette");
  // New messages and reassignments arrive by themselves; no refresh needed.
  useAgentLiveUpdates(!!user);
  // The drawer is "open for" a pathname, so it closes by itself on navigation
  // without an effect that sets state.
  const [openFor, setOpenFor] = useState<string | null>(null);
  const open = openFor === pathname;
  const setOpen = (v: boolean) => setOpenFor(v ? pathname : null);
  const drawerRef = useRef<HTMLElement>(null);
  useFocusTrap(drawerRef, open);
  useEscape(open, () => setOpenFor(null));

  const items = NAV.filter(
    (n) =>
      (!("admin" in n && n.admin) || perms.manageSettings) &&
      (!("reports" in n && n.reports) || hasAction(user, "reports", perms.viewReports)) &&
      (!("campaigns" in n && n.campaigns) || canManageCampaigns(user)),
  );

  const nav = (
    <nav aria-label="Primary" className="flex flex-col gap-0.5">
      <p className="px-3 pb-2 text-[10.5px] font-semibold tracking-[0.12em] text-subtle-foreground uppercase">
        Workspace
      </p>
      {items.map((n) => {
        const base = "match" in n ? n.match : n.href;
        const active = pathname === base || pathname.startsWith(`${base}/`);
        return (
          <Link
            key={n.href}
            href={n.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-[13.5px] font-medium transition-colors focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:outline-none",
              active
                ? "bg-primary/10 text-primary-ink"
                : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
            )}
          >
            {active && (
              <span
                aria-hidden
                className="absolute top-1/2 -left-3 h-5 w-0.75 -translate-y-1/2 rounded-r-full bg-primary"
              />
            )}
            <n.icon
              className={cn(
                "size-4.5 shrink-0 transition-colors",
                active ? "text-primary-ink" : "text-subtle-foreground group-hover:text-foreground",
              )}
              strokeWidth={active ? 2.25 : 1.85}
            />
            {n.label}
          </Link>
        );
      })}
    </nav>
  );

  const footer = (
    <div className="mt-auto space-y-3 border-t border-border pt-3">
      <div className="flex items-center gap-2.5 px-2">
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-linear-to-br from-[#a99bff] to-[#5b3cf0] text-xs font-bold text-white">
          {initials(user?.name)}
        </span>
        <div className="min-w-0">
          <div className="truncate text-[13px] font-medium text-foreground">
            {user?.name ?? "…"}
          </div>
          <div className="truncate text-[11px] text-muted-foreground">
            {user ? ROLE_LABEL[user.role] : ""}
            {user?.workspace_name ? ` · ${user.workspace_name}` : ""}
          </div>
        </div>
      </div>
    </div>
  );

  const newTicket = perms.createTicket && (
    <Link href="/tickets/new">
      <Button className="w-full shadow-sm" size="md">
        <Plus className="size-4" /> New ticket
      </Button>
    </Link>
  );

  return (
    <div className="min-h-svh lg:grid lg:grid-cols-[15rem_minmax(0,1fr)]">
      <aside className="sticky top-0 hidden h-svh flex-col gap-6 border-r border-border bg-surface px-3 py-5 lg:flex">
        <Link href="/tickets" className="px-2" aria-label="CeDesk home">
          <Logo />
        </Link>
        {newTicket}
        {nav}
        {footer}
      </aside>

      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            tabIndex={-1}
            aria-label="Close menu"
            className="absolute inset-0 bg-black/40 backdrop-blur-sm"
            onClick={() => setOpen(false)}
          />
          <aside
            ref={drawerRef}
            tabIndex={-1}
            aria-label="Menu"
            className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] animate-rise flex-col gap-6 bg-surface px-3 py-4 outline-none"
          >
            <div className="flex items-center justify-between px-2">
              <Logo />
              <button
                onClick={() => setOpen(false)}
                aria-label="Close menu"
                className="grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-surface-2"
              >
                <X className="size-4" />
              </button>
            </div>
            {newTicket}
            {nav}
            {footer}
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-col">
        <OfflineBanner />
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border bg-background/85 px-3 backdrop-blur-md sm:px-5">
          <button
            onClick={() => setOpen(true)}
            aria-label="Open menu"
            className="grid size-9 place-items-center rounded-lg text-muted-foreground hover:bg-surface-2 lg:hidden"
          >
            <Menu className="size-5" />
          </button>
          <span className="lg:hidden">
            <Logo className="[&>span:last-child]:text-base" />
          </span>
          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={() => window.dispatchEvent(new Event("cedesk:palette"))}
              className="mr-1 hidden h-9 items-center gap-2 rounded-lg border border-border bg-surface px-3 text-[13px] text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground sm:inline-flex"
            >
              <Search className="size-3.5" aria-hidden />
              {t("button")}
              <kbd className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px]">Ctrl K</kbd>
            </button>
            <ResourcesMenu label="Help" newTab triggerClassName="hidden sm:inline-flex" />
            <WorkMenu />
            <NotificationBell />
            <UserMenu />
          </div>
        </header>
        <main id="main" className="min-w-0 flex-1">
          {children}
        </main>
      </div>
      <CommandPalette />
    </div>
  );
}

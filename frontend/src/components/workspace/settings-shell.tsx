"use client";

import {
  type LucideIcon,
  BookOpen,
  Bot,
  Building2,
  CircleCheck,
  ChevronRight,
  Code2,
  Contact,
  Download,
  FlaskConical,
  FileClock,
  GitFork,
  Gauge,
  Globe,
  KeyRound,
  LifeBuoy,
  ListChecks,
  Lock,
  Mail,
  Plug,
  Radio,
  Search,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Tags,
  Timer,
  TriangleAlert,
  UserCog,
  Users,
  UsersRound,
  Wand2,
  Webhook,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyState, Skeleton } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { cn } from "@/lib/utils";

type Item = { href: string; label: string; icon: LucideIcon; description: string };
type Group = { label: string; icon: LucideIcon; items: Item[] };

const GROUPS: Group[] = [
  {
    label: "Workspace",
    icon: Building2,
    items: [
      {
        href: "/settings/members",
        label: "Members",
        icon: Users,
        description: "Invite people and manage who has access to your workspace.",
      },
      {
        href: "/settings/teams",
        label: "Teams",
        icon: UsersRound,
        description: "Group agents into teams to route and own conversations.",
      },
      {
        href: "/settings/roles",
        label: "Roles",
        icon: UserCog,
        description: "Control what each role can see and do.",
      },
      {
        href: "/settings/ticket-types",
        label: "Ticket types",
        icon: Tags,
        description: "Define the kinds of requests your team handles.",
      },
    ],
  },
  {
    label: "Service",
    icon: LifeBuoy,
    items: [
      {
        href: "/settings/sla",
        label: "SLA",
        icon: Timer,
        description: "Set response and resolution targets for your tickets.",
      },
      {
        href: "/settings/rules",
        label: "Rules",
        icon: Workflow,
        description: "Automate assignment, tagging and follow-ups.",
      },
      {
        href: "/settings/auto-close",
        label: "Auto-close",
        icon: CircleCheck,
        description: "Resolve Pending tickets when the customer stops replying.",
      },
      {
        href: "/settings/workflows",
        label: "Workflows",
        icon: GitFork,
        description: "Chains of steps that can wait, branch and follow up on their own.",
      },
      {
        href: "/settings/automation/log",
        label: "Automation log",
        icon: ListChecks,
        description: "See what rules and workflows did, and stop a waiting run.",
      },
      {
        href: "/settings/macros",
        label: "Macros",
        icon: Wand2,
        description: "One-click actions that apply several changes at once.",
      },
      {
        href: "/settings/saved-replies",
        label: "Saved replies",
        icon: Zap,
        description: "Reusable answers your agents can drop into a reply.",
      },
      {
        href: "/settings/topics",
        label: "Topics & redaction",
        icon: ShieldCheck,
        description: "Classify conversations and hide sensitive data.",
      },
    ],
  },
  {
    label: "Customers",
    icon: Contact,
    items: [
      {
        href: "/settings/contact-fields",
        label: "Contact fields",
        icon: SlidersHorizontal,
        description: "Extra details to keep on every customer, like a plan or account number.",
      },
      {
        href: "/settings/portal",
        label: "Portal",
        icon: Globe,
        description: "Customise the portal where customers track their requests.",
      },
      {
        href: "/settings/help-center",
        label: "Help center",
        icon: BookOpen,
        description: "Publish articles so customers can help themselves.",
      },
    ],
  },
  {
    label: "Channels",
    icon: Radio,
    items: [
      {
        href: "/settings/email",
        label: "Email",
        icon: Mail,
        description: "Connect mailboxes and manage how email becomes tickets.",
      },
      {
        href: "/settings/cevanos",
        label: "Cevanos",
        icon: Bot,
        description: "Configure your AI assistant and how it responds.",
      },
      {
        href: "/settings/failed",
        label: "Failed events",
        icon: TriangleAlert,
        description: "Review and retry events that did not go through.",
      },
      {
        href: "/settings/imports",
        label: "Imports",
        icon: Download,
        description: "Move tickets over from another help desk.",
      },
    ],
  },
  {
    label: "Developers",
    icon: Code2,
    items: [
      {
        href: "/settings/apps",
        label: "Apps",
        icon: Plug,
        description: "Install and manage connected apps.",
      },
      {
        href: "/settings/api-keys",
        label: "API keys",
        icon: KeyRound,
        description: "Create keys to access the API securely.",
      },
      {
        href: "/settings/webhooks",
        label: "Webhooks",
        icon: Webhook,
        description: "Send events to your own systems in real time.",
      },
    ],
  },
  {
    label: "Security & data",
    icon: Lock,
    items: [
      {
        href: "/settings/security",
        label: "Security",
        icon: Lock,
        description: "Sign-in policies and protection for your workspace.",
      },
      {
        href: "/settings/privacy",
        label: "Privacy",
        icon: ShieldCheck,
        description: "Manage personal data, exports and deletion requests.",
      },
      {
        href: "/settings/audit",
        label: "Audit log",
        icon: FileClock,
        description: "A record of who changed what, and when.",
      },
      {
        href: "/settings/plan",
        label: "Plan & retention",
        icon: Gauge,
        description: "Your plan, usage and how long data is kept.",
      },
      {
        href: "/settings/sandbox",
        label: "Sandbox",
        icon: FlaskConical,
        description: "Try rule, macro and saved-reply changes before they go live.",
      },
      {
        href: "/settings/demo",
        label: "Demo data",
        icon: FlaskConical,
        description: "Load sample tickets to try CeDesk out.",
      },
    ],
  },
];

/**
 * A horizontally scrollable row that fades out at whichever edge has more content,
 * so it is obvious it can be swiped, and keeps the current item in view.
 */
function ScrollFade({
  className,
  children,
  as: Tag = "div",
  ...rest
}: React.HTMLAttributes<HTMLElement> & { as?: "div" | "nav" }) {
  const ref = useRef<HTMLElement>(null);
  const [edge, setEdge] = useState({ left: false, right: false });
  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const left = el.scrollLeft > 4;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 4;
    setEdge((e) => (e.left === left && e.right === right ? e : { left, right }));
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const active = el.querySelector<HTMLElement>('[aria-current="page"]');
    if (active) {
      el.scrollLeft = Math.max(0, active.offsetLeft - (el.clientWidth - active.offsetWidth) / 2);
    }
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  });
  const fade = `linear-gradient(to right, ${edge.left ? "transparent, #000 2.5rem" : "#000, #000"}, ${
    edge.right ? "#000 calc(100% - 2.5rem), transparent" : "#000, #000"
  })`;
  return (
    <Tag
      {...rest}
      ref={ref as React.Ref<never>}
      onScroll={measure}
      style={edge.left || edge.right ? { maskImage: fade, WebkitMaskImage: fade } : undefined}
      className={className}
    >
      {children}
    </Tag>
  );
}

/** Settings are Admin only. The API enforces it too; this just hides the UI. */
export function SettingsShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { perms, isPending } = useCurrentUser();
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return GROUPS;
    return GROUPS.map((g) => ({
      ...g,
      items: g.items.filter(
        (i) => i.label.toLowerCase().includes(q) || i.description.toLowerCase().includes(q),
      ),
    })).filter((g) => g.items.length > 0);
  }, [query]);

  if (isPending) return <Skeleton className="m-6 h-40" />;
  if (!perms.manageSettings) {
    return (
      <EmptyState icon={<ShieldAlert className="size-6" />} title="Admins only">
        You don&apos;t have access to workspace settings.
      </EmptyState>
    );
  }

  const currentGroup = GROUPS.find((g) => g.items.some((i) => pathname.startsWith(i.href)));
  const current = currentGroup?.items.find((i) => pathname.startsWith(i.href));
  const CurrentIcon = current?.icon;
  const activeGroup: Group = currentGroup ?? (GROUPS[0] as Group);
  const searching = query.trim().length > 0;
  // Searching looks across every tab; otherwise only the selected tab's items show.
  const visibleGroups = searching ? filtered : [activeGroup];

  return (
    <div className="mx-auto w-full max-w-6xl px-3 py-5 sm:px-6 sm:py-8">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Manage your workspace, channels, security and integrations.
        </p>
      </div>

      <ScrollFade
        as="nav"
        aria-label="Settings sections"
        className="mt-5 flex [scrollbar-width:none] gap-1 overflow-x-auto overflow-y-hidden border-b border-border [&::-webkit-scrollbar]:hidden"
      >
        {GROUPS.map((g) => {
          const active = g.label === activeGroup.label;
          const Icon = g.icon;
          return (
            <Link
              key={g.label}
              href={g.items[0]?.href ?? "/settings/members"}
              aria-current={active ? "page" : undefined}
              onClick={() => setQuery("")}
              className={cn(
                "relative flex items-center gap-2 px-3.5 py-3 text-sm font-medium whitespace-nowrap transition-colors",
                active ? "text-primary-ink" : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="size-4" aria-hidden />
              {g.label}
              <span
                aria-hidden
                className={cn(
                  "absolute inset-x-2 bottom-0 h-0.5 rounded-full transition-colors",
                  active ? "bg-primary" : "bg-transparent",
                )}
              />
            </Link>
          );
        })}
      </ScrollFade>

      <div className="mt-6 grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[16rem_minmax(0,1fr)] lg:gap-8">
        <aside className="min-w-0 lg:sticky lg:top-20 lg:self-start">
          <nav
            aria-label="Settings pages"
            className="rounded-2xl border border-border bg-surface p-3 shadow-card lg:max-h-[calc(100vh-7rem)] lg:overflow-y-auto"
          >
            <div className="relative mb-3">
              <Search
                className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-subtle-foreground"
                aria-hidden
              />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search settings"
                aria-label="Search settings"
                className="h-9 w-full rounded-lg border border-border bg-surface-2 pr-8 pl-9 text-sm transition-colors outline-none placeholder:text-subtle-foreground focus:border-primary focus:bg-surface focus:ring-2 focus:ring-primary/20"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  aria-label="Clear search"
                  className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-0.5 text-subtle-foreground hover:text-foreground"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </div>

            <ScrollFade className="flex [scrollbar-width:none] gap-1 overflow-x-auto pb-1 lg:flex-col lg:gap-4 lg:overflow-visible lg:pb-0 [&::-webkit-scrollbar]:hidden">
              {searching && filtered.length === 0 && (
                <p className="px-2 py-3 text-sm text-muted-foreground">
                  No settings match &ldquo;{query}&rdquo;.
                </p>
              )}
              {visibleGroups.map((g) => (
                <div key={g.label} className="flex gap-1 lg:block">
                  <p className="hidden px-2.5 pb-1.5 text-[11px] font-bold tracking-[0.08em] text-subtle-foreground uppercase lg:block">
                    {searching ? g.label : `${g.label} settings`}
                  </p>
                  <ul className="flex gap-1 lg:flex-col">
                    {g.items.map((t) => {
                      const active = pathname.startsWith(t.href);
                      const Icon = t.icon;
                      return (
                        <li key={t.href}>
                          <Link
                            href={t.href}
                            aria-current={active ? "page" : undefined}
                            className={cn(
                              "group relative flex items-center gap-2.5 rounded-xl px-2 py-1.5 text-sm font-medium whitespace-nowrap transition-all",
                              active
                                ? "bg-primary-soft text-primary-ink"
                                : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
                            )}
                          >
                            {active && (
                              <span
                                aria-hidden
                                className="absolute top-1/2 -left-3 hidden h-5 w-1 -translate-y-1/2 rounded-r-full bg-primary lg:block"
                              />
                            )}
                            <span
                              className={cn(
                                "grid size-7 flex-none place-items-center rounded-lg transition-colors",
                                active
                                  ? "bg-primary text-primary-foreground shadow-card"
                                  : "bg-surface-2 text-muted-foreground group-hover:bg-surface group-hover:text-foreground",
                              )}
                            >
                              <Icon className="size-3.5" aria-hidden />
                            </span>
                            {t.label}
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
            </ScrollFade>
          </nav>
        </aside>

        <div className="min-w-0">
          {current && CurrentIcon && (
            <header className="mb-5 rounded-2xl border border-border bg-surface p-4 shadow-card sm:p-5">
              <div className="flex items-center gap-1 text-xs font-medium text-subtle-foreground">
                <span>Settings</span>
                <ChevronRight className="size-3" aria-hidden />
                <span>{currentGroup?.label}</span>
              </div>
              <div className="mt-3 flex items-center gap-3.5">
                <span className="grid size-11 flex-none place-items-center rounded-xl bg-primary-soft text-primary-ink">
                  <CurrentIcon className="size-5" aria-hidden />
                </span>
                <div className="min-w-0">
                  <h2 className="text-lg font-semibold tracking-tight">{current.label}</h2>
                  <p className="text-sm text-muted-foreground">{current.description}</p>
                </div>
              </div>
            </header>
          )}
          {children}
        </div>
      </div>
    </div>
  );
}

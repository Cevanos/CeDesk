"use client";

import {
  type LucideIcon,
  Bot,
  Mail,
  ShieldAlert,
  Tags,
  Timer,
  TriangleAlert,
  Users,
  UsersRound,
  Wand2,
  Workflow,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { EmptyState, Skeleton } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { cn } from "@/lib/utils";

const GROUPS: {
  label: string;
  items: { href: string; label: string; icon: LucideIcon }[];
}[] = [
  {
    label: "Workspace",
    items: [
      { href: "/settings/members", label: "Members", icon: Users },
      { href: "/settings/teams", label: "Teams", icon: UsersRound },
      { href: "/settings/ticket-types", label: "Ticket types", icon: Tags },
    ],
  },
  {
    label: "Service",
    items: [
      { href: "/settings/sla", label: "SLA", icon: Timer },
      { href: "/settings/rules", label: "Rules", icon: Workflow },
      { href: "/settings/macros", label: "Macros", icon: Wand2 },
      { href: "/settings/saved-replies", label: "Saved replies", icon: Zap },
    ],
  },
  {
    label: "Channels",
    items: [
      { href: "/settings/email", label: "Email", icon: Mail },
      { href: "/settings/cevanos", label: "Cevanos", icon: Bot },
      { href: "/settings/failed", label: "Failed events", icon: TriangleAlert },
    ],
  },
];

/** Settings are Admin only. The API enforces it too; this just hides the UI. */
export function SettingsShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { perms, isPending } = useCurrentUser();

  if (isPending) return <Skeleton className="m-6 h-40" />;
  if (!perms.manageSettings) {
    return (
      <EmptyState icon={<ShieldAlert className="size-6" />} title="Admins only">
        You don&apos;t have access to workspace settings.
      </EmptyState>
    );
  }

  const current = GROUPS.flatMap((g) => g.items).find((i) => pathname.startsWith(i.href));

  return (
    <div className="mx-auto w-full max-w-6xl px-3 py-5 sm:px-6 sm:py-8">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
      <div className="mt-6 grid gap-6 lg:grid-cols-[13.5rem_minmax(0,1fr)] lg:gap-10">
        <nav aria-label="Settings" className="lg:sticky lg:top-20 lg:self-start">
          <div className="flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:gap-5 lg:overflow-visible lg:pb-0">
            {GROUPS.map((g) => (
              <div key={g.label} className="flex gap-1 lg:block">
                <p className="hidden px-2.5 pb-1.5 text-[11px] font-bold tracking-[0.08em] text-subtle-foreground uppercase lg:block">
                  {g.label}
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
                            "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium whitespace-nowrap transition-colors",
                            active
                              ? "bg-primary-soft text-primary-ink shadow-card"
                              : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
                          )}
                        >
                          <Icon className="size-4 flex-none" aria-hidden />
                          {t.label}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        </nav>
        <div className="min-w-0">
          {current && (
            <h2 className="mb-4 text-lg font-semibold tracking-tight lg:hidden">{current.label}</h2>
          )}
          {children}
        </div>
      </div>
    </div>
  );
}

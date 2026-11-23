"use client";

import { BookOpen, LifeBuoy, MessageSquareText, Ticket } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";

import { Logo, ThemeToggle } from "@/components/ui/brand";
import { useContactFormOff } from "@/hooks/use-portal";
import { cn } from "@/lib/utils";

/** "acme-store" -> "Acme Store". The real name is shown on pages that load it. */
export function prettySlug(slug: string) {
  return slug
    .split(/[-_.]+/)
    .filter(Boolean)
    .map((w) => w[0]!.toUpperCase() + w.slice(1))
    .join(" ");
}

/**
 * Frame for every page a customer opens: no agent login, no agent navigation. Soft brand
 * glow behind a sticky glass header, a readable content column and a quiet footer.
 */
export function PublicShell({ children }: { children: React.ReactNode }) {
  const { workspace } = useParams<{ workspace?: string }>();
  const pathname = usePathname();
  const formOff = useContactFormOff(workspace ?? "");

  const nav = workspace
    ? [
        { href: `/${workspace}/help`, label: "Help center", icon: BookOpen, match: "/help" },
        ...(formOff
          ? []
          : [
              {
                href: `/${workspace}/contact`,
                label: "Contact us",
                icon: MessageSquareText,
                match: "/contact",
              },
            ]),
        { href: `/${workspace}/portal`, label: "My tickets", icon: Ticket, match: "/portal" },
      ]
    : [];

  return (
    <div className="relative isolate flex min-h-svh flex-col overflow-x-clip">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[34rem]">
        <div className="absolute inset-0 bg-[radial-gradient(60rem_26rem_at_85%_-10%,rgb(119_98_251/0.22),transparent_62%),radial-gradient(44rem_24rem_at_0%_-5%,rgb(47_123_255/0.14),transparent_62%)]" />
        <div className="absolute -top-24 -right-16 size-80 animate-drift rounded-full bg-primary/15 blur-3xl" />
        <div className="absolute top-10 -left-24 size-72 animate-drift rounded-full bg-[#2f7bff]/10 blur-3xl [animation-direction:reverse]" />
      </div>

      <header className="sticky top-0 z-30 border-b border-border/70 bg-background/75 backdrop-blur-xl">
        <div className="mx-auto flex h-16 w-full max-w-5xl items-center gap-3 px-4 sm:px-6">
          <Link
            href={workspace ? `/${workspace}/help` : "/"}
            aria-label="Support home"
            className="flex min-w-0 items-center gap-3 rounded-lg focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:outline-none"
          >
            <Logo />
            {workspace && (
              <span className="hidden min-w-0 items-center gap-3 sm:flex">
                <span aria-hidden className="h-5 w-px bg-border-strong" />
                <span className="truncate text-sm font-medium text-muted-foreground">
                  {prettySlug(workspace)} support
                </span>
              </span>
            )}
          </Link>

          {nav.length > 0 && (
            <nav aria-label="Support" className="ml-auto hidden items-center gap-1 md:flex">
              {nav.map((n) => {
                const active = pathname.includes(n.match);
                return (
                  <Link
                    key={n.href}
                    href={n.href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "inline-flex h-9 items-center gap-2 rounded-lg px-3 text-[13.5px] font-medium transition-colors focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:outline-none",
                      active
                        ? "bg-primary/10 text-primary-ink"
                        : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
                    )}
                  >
                    <n.icon className="size-4" aria-hidden />
                    {n.label}
                  </Link>
                );
              })}
            </nav>
          )}
          <ThemeToggle className={nav.length ? "md:ml-2" : "ml-auto"} />
        </div>

        {nav.length > 0 && (
          <nav
            aria-label="Support"
            className="mx-auto flex max-w-5xl gap-1 overflow-x-auto px-3 pb-2 md:hidden"
          >
            {nav.map((n) => {
              const active = pathname.includes(n.match);
              return (
                <Link
                  key={n.href}
                  href={n.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "inline-flex h-9 shrink-0 items-center gap-2 rounded-full px-3.5 text-[13px] font-medium whitespace-nowrap",
                    active
                      ? "bg-primary text-primary-foreground"
                      : "bg-surface-2 text-muted-foreground",
                  )}
                >
                  <n.icon className="size-3.5" aria-hidden />
                  {n.label}
                </Link>
              );
            })}
          </nav>
        )}
      </header>

      <main id="main" className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
        {children}
      </main>

      <footer className="border-t border-border/70 py-6 text-center text-xs text-subtle-foreground">
        <span className="inline-flex items-center gap-1.5">
          <LifeBuoy className="size-3.5" aria-hidden /> Powered by CeDesk · © {new Date().getFullYear()}{" "}
          Cogninest AI
        </span>
      </footer>
    </div>
  );
}

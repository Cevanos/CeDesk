import { ArrowUpRight } from "lucide-react";
import type { Metadata } from "next";
import { cookies } from "next/headers";
import Link from "next/link";

import { DocsMobileMenu } from "@/components/docs/docs-mobile-menu";
import { DocsSearch } from "@/components/docs/docs-search";
import { ResourcesMenu } from "@/components/layout/resources-menu";
import { Logo, ThemeToggle } from "@/components/ui/brand";
import { getSections } from "@/lib/server/docs";

import "./docs.css";

export const metadata: Metadata = {
  title: { default: "Documentation", template: "%s · CeDesk Docs" },
  description:
    "Guides, how-tos and the full API reference for CeDesk, the help desk where people and AI resolve customer conversations together.",
};

const NAV = [
  { href: "/docs", label: "User guide" },
  { href: "/docs/how-to", label: "How-to" },
  { href: "/docs/developers", label: "Developers" },
  { href: "/docs/api", label: "API reference" },
  { href: "/docs/glossary", label: "Glossary" },
];

export default async function DocsLayout({ children }: { children: React.ReactNode }) {
  const signedIn = Boolean((await cookies()).get("cedesk_session")?.value);
  const topics = (await getSections()).map((s) => ({ href: `/docs/${s.slug}`, label: s.title }));
  return (
    <div className="flex min-h-svh flex-col bg-background pt-16">
      <header className="fixed inset-x-0 top-0 z-50 border-b border-border/80 bg-background/85 shadow-[0_1px_0_0_rgb(255_255_255/0.6),0_8px_24px_-16px_rgb(11_27_54/0.25)] backdrop-blur-xl backdrop-saturate-150">
        <div
          aria-hidden
          className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/60 to-transparent"
        />
        <div className="mx-auto flex h-16 max-w-[90rem] items-center gap-2 px-[max(1rem,env(safe-area-inset-left))] sm:gap-4 sm:px-6">
          <Link
            href="/?landing=1"
            className="flex min-w-0 items-center gap-3"
            aria-label="CeDesk home"
          >
            <Logo />
            <span className="hidden rounded-full border border-primary/25 bg-primary-soft px-2.5 py-0.5 text-xs font-semibold text-primary-ink sm:inline">
              Docs
            </span>
          </Link>
          <nav
            aria-label="Documentation"
            className="ml-2 hidden items-center gap-0.5 lg:flex xl:ml-6"
          >
            {NAV.map((n) => (
              <Link
                key={n.href}
                href={n.href}
                className="rounded-full px-3 py-1.5 text-sm font-medium whitespace-nowrap text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground xl:px-3.5"
              >
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <DocsSearch />
            <div className="hidden 2xl:block">
              <ResourcesMenu align="right" triggerClassName="rounded-full px-3 py-1.5 h-9" />
            </div>
            <ThemeToggle className="hidden md:inline-flex" />
            {signedIn ? (
              <Link
                href="/tickets"
                className="inline-flex h-9 items-center gap-1 rounded-full bg-primary px-4 text-sm font-semibold whitespace-nowrap text-primary-foreground shadow-[0_6px_16px_-6px_var(--primary)] transition hover:bg-primary-hover"
              >
                Dashboard
                <ArrowUpRight className="size-3.5" aria-hidden />
              </Link>
            ) : (
              <>
                <Link
                  href="/login"
                  className="hidden h-9 items-center rounded-full px-3.5 text-sm font-medium whitespace-nowrap text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground md:inline-flex"
                >
                  Sign in
                </Link>
                <Link
                  href="/signup"
                  className="hidden h-9 items-center gap-1 rounded-full bg-primary px-4 text-sm font-semibold whitespace-nowrap text-primary-foreground shadow-[0_6px_16px_-6px_var(--primary)] transition hover:bg-primary-hover sm:inline-flex"
                >
                  Get started
                  <ArrowUpRight className="size-3.5" aria-hidden />
                </Link>
              </>
            )}
            <DocsMobileMenu nav={NAV} topics={topics} />
          </div>
        </div>
      </header>

      <main id="main" className="flex-1">
        {children}
      </main>

      <footer className="border-t border-border bg-surface">
        <div className="mx-auto flex max-w-[90rem] flex-col items-center justify-between gap-3 px-6 pt-8 pb-[max(2rem,env(safe-area-inset-bottom))] text-center text-sm text-muted-foreground sm:flex-row sm:text-left">
          <Logo className="scale-90" />
          <p>
            © {new Date().getFullYear()} CeDesk by Cevanos. Built to help teams answer faster.
          </p>
          <div className="flex flex-wrap justify-center gap-x-5 gap-y-2">
            <Link href="/?landing=1" className="hover:text-foreground">
              Home
            </Link>
            {signedIn ? (
              <Link href="/tickets" className="hover:text-foreground">
                Dashboard
              </Link>
            ) : (
              <>
                <Link href="/login" className="hover:text-foreground">
                  Sign in
                </Link>
                <Link href="/signup" className="hover:text-foreground">
                  Sign up
                </Link>
              </>
            )}
          </div>
        </div>
      </footer>
    </div>
  );
}

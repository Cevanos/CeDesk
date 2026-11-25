"use client";

import { ArrowUpRight, Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";
import { createPortal } from "react-dom";

import { ThemeToggle } from "@/components/ui/brand";
import { cn } from "@/lib/utils";

type NavItem = { href: string; label: string };

/**
 * Full-height menu for screens below `lg`. It is tied to the path it was opened on, so any
 * navigation closes it without an effect.
 */
export function DocsMobileMenu({ nav, topics }: { nav: NavItem[]; topics: NavItem[] }) {
  const pathname = usePathname();
  const [openFor, setOpenFor] = React.useState<string | null>(null);
  const open = openFor === pathname;

  React.useEffect(() => {
    if (!open) return;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpenFor(null);
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const link =
    "flex min-h-11 items-center rounded-xl px-3 text-[15px] font-medium text-foreground-2 hover:bg-surface-2";

  return (
    <>
      <button
        type="button"
        aria-label={open ? "Close menu" : "Open menu"}
        aria-expanded={open}
        aria-controls="docs-mobile-menu"
        onClick={() => setOpenFor(open ? null : pathname)}
        className="inline-flex size-11 items-center justify-center rounded-full text-foreground hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none lg:hidden"
      >
        {open ? <X className="size-5" aria-hidden /> : <Menu className="size-5" aria-hidden />}
      </button>

      {/* Portalled: the header's backdrop-filter would otherwise clip a fixed child to its own box. */}
      {open &&
        createPortal(
          <div
            id="docs-mobile-menu"
            className="fixed inset-x-0 top-16 bottom-0 z-40 overflow-y-auto overscroll-contain border-t border-border bg-background px-4 pt-4 pb-[max(1.5rem,env(safe-area-inset-bottom))] lg:hidden"
          >
            <nav aria-label="Documentation" className="space-y-0.5">
              {nav.map((n) => (
                <Link key={n.href} href={n.href} className={link}>
                  {n.label}
                </Link>
              ))}
            </nav>
            <p className="mt-6 mb-2 px-3 text-xs font-semibold tracking-wider text-subtle-foreground uppercase">
              Topics
            </p>
            <nav aria-label="Topics" className="grid gap-0.5 sm:grid-cols-2">
              {topics.map((t) => (
                <Link key={t.href} href={t.href} className={cn(link, "text-muted-foreground")}>
                  {t.label}
                </Link>
              ))}
            </nav>
            <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-border pt-5">
              <ThemeToggle />
              <Link
                href="/login"
                className="inline-flex h-11 items-center rounded-full border border-border px-5 text-sm font-medium"
              >
                Sign in
              </Link>
              <Link
                href="/signup"
                className="inline-flex h-11 items-center gap-1 rounded-full bg-primary px-5 text-sm font-semibold text-primary-foreground"
              >
                Get started <ArrowUpRight className="size-3.5" aria-hidden />
              </Link>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}

"use client";

import { CornerDownLeft, FileText, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { cn } from "@/lib/utils";

type Entry = { href: string; title: string; text: string; section: string };

function score(e: Entry, terms: string[]): number {
  const title = e.title.toLowerCase();
  const text = e.text.toLowerCase();
  let s = 0;
  for (const t of terms) {
    if (title.includes(t)) s += title.startsWith(t) ? 12 : 8;
    else if (text.includes(t)) s += 2;
    else return 0;
  }
  return s;
}

function snippet(text: string, terms: string[]): string {
  const i = Math.max(0, text.toLowerCase().indexOf(terms[0] ?? ""));
  const start = Math.max(0, i - 40);
  return (
    (start > 0 ? "…" : "") + text.slice(start, start + 140) + (text.length > start + 140 ? "…" : "")
  );
}

export function DocsSearch({ variant = "button" }: { variant?: "button" | "hero" }) {
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const [index, setIndex] = React.useState<Entry[] | null>(null);
  const [active, setActive] = React.useState(0);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (
        e.key === "/" &&
        !/input|textarea/i.test((e.target as HTMLElement | null)?.tagName ?? "")
      ) {
        e.preventDefault();
        setOpen(true);
      } else if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  React.useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    document.body.style.overflow = "hidden";
    if (!index) {
      fetch("/docs/search.json")
        .then((r) => r.json())
        .then(setIndex)
        .catch(() => setIndex([]));
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open, index]);

  const terms = React.useMemo(() => query.toLowerCase().split(/\s+/).filter(Boolean), [query]);
  const results = React.useMemo(() => {
    if (!index || terms.length === 0) return [];
    return index
      .map((e) => ({ e, s: score(e, terms) }))
      .filter((r) => r.s > 0)
      .sort((a, b) => b.s - a.s)
      .slice(0, 8)
      .map((r) => r.e);
  }, [index, terms]);

  const go = (href: string) => {
    setOpen(false);
    setQuery("");
    router.push(href);
  };

  return (
    <>
      {variant === "hero" ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="group mx-auto flex h-14 w-full max-w-xl items-center gap-3 rounded-2xl border border-border-strong bg-surface/80 px-5 text-left text-base text-muted-foreground shadow-lg backdrop-blur transition hover:border-primary/50 hover:shadow-xl focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <Search className="size-5 text-primary-ink" aria-hidden />
          <span className="flex-1">Search the documentation…</span>
          <kbd className="hidden rounded-md border border-border bg-surface-2 px-2 py-0.5 font-mono text-xs sm:inline">
            Ctrl K
          </kbd>
        </button>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Search the documentation"
          className="flex h-11 min-w-11 items-center justify-center gap-2 rounded-full border border-border bg-surface/70 px-3 text-sm text-muted-foreground transition hover:border-primary/40 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none sm:h-9 sm:min-w-0 sm:justify-start lg:w-44 xl:w-60"
        >
          <Search className="size-4" aria-hidden />
          <span className="hidden flex-1 text-left whitespace-nowrap lg:inline">Search docs</span>
          <kbd className="hidden rounded border border-border bg-surface-2 px-1.5 font-mono text-[11px] whitespace-nowrap xl:inline">
            Ctrl K
          </kbd>
        </button>
      )}

      {open && (
        // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
        <div
          className="fixed inset-0 z-[100] flex items-start justify-center bg-[#0b1b36]/50 p-3 pt-[max(4.5rem,8dvh)] backdrop-blur-sm sm:p-4 sm:pt-[12dvh]"
          onMouseDown={(e) => e.target === e.currentTarget && setOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Search the documentation"
        >
          <div className="w-full max-w-xl overflow-hidden rounded-2xl border border-border-strong bg-surface shadow-2xl">
            <div className="flex items-center gap-3 border-b border-border px-4">
              <Search className="size-5 text-primary-ink" aria-hidden />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setActive(0);
                }}
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setActive((a) => Math.min(a + 1, results.length - 1));
                  } else if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setActive((a) => Math.max(a - 1, 0));
                  } else if (e.key === "Enter" && results[active]) go(results[active].href);
                }}
                placeholder="Search articles, settings, API routes…"
                className="h-14 flex-1 bg-transparent text-base text-foreground outline-none placeholder:text-subtle-foreground"
              />
              <kbd className="rounded border border-border bg-surface-2 px-1.5 font-mono text-[11px] text-muted-foreground">
                esc
              </kbd>
            </div>
            <ul className="max-h-[min(50dvh,26rem)] overflow-y-auto p-2">
              {terms.length === 0 && (
                <li className="px-3 py-8 text-center text-sm text-muted-foreground">
                  Type to search every article and API route.
                </li>
              )}
              {terms.length > 0 && index && results.length === 0 && (
                <li className="px-3 py-8 text-center text-sm text-muted-foreground">
                  Nothing found for “{query}”.
                </li>
              )}
              {results.map((r, i) => (
                <li key={r.href}>
                  <button
                    type="button"
                    onMouseEnter={() => setActive(i)}
                    onClick={() => go(r.href)}
                    className={cn(
                      "flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left",
                      i === active ? "bg-primary-soft" : "hover:bg-surface-2",
                    )}
                  >
                    <FileText className="mt-0.5 size-4 shrink-0 text-primary-ink" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2">
                        <span className="truncate text-sm font-semibold text-foreground">
                          {r.title}
                        </span>
                        <span className="shrink-0 rounded-full bg-surface-3 px-2 py-px text-[11px] text-muted-foreground">
                          {r.section}
                        </span>
                      </span>
                      <span className="mt-0.5 line-clamp-2 block text-xs text-muted-foreground">
                        {snippet(r.text, terms)}
                      </span>
                    </span>
                    {i === active && (
                      <CornerDownLeft className="mt-1 size-4 text-primary-ink" aria-hidden />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </>
  );
}

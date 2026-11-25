"use client";

import {
  BookOpen,
  Braces,
  ChevronDown,
  CircleHelp,
  History,
  KeyRound,
  LifeBuoy,
  Rocket,
  Sparkles,
  TerminalSquare,
  Webhook,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

type Item = { icon: LucideIcon; title: string; body: string; href: string };
type Column = { heading: string; items: Item[] };

/**
 * Swagger UI is served by the backend, not this app, and on a shared host `/docs` belongs to
 * these docs. Set NEXT_PUBLIC_SWAGGER_URL to the full address to show the link; no guessing.
 */
function swaggerUrl(): string | null {
  const url = (process.env.NEXT_PUBLIC_SWAGGER_URL ?? "").trim();
  return /^https?:\/\//.test(url) ? url : null;
}

function columns(): Column[] {
  const swagger = swaggerUrl();
  return [
    {
      heading: "Learn",
      items: [
        {
          icon: BookOpen,
          title: "User guide",
          body: "Step-by-step articles for every part of CeDesk",
          href: "/docs",
        },
        {
          icon: Rocket,
          title: "Getting started",
          body: "Create your workspace and try it with sample data",
          href: "/docs/getting-started",
        },
        {
          icon: Workflow,
          title: "How-to recipes",
          body: "Short answers for common jobs",
          href: "/docs/how-to",
        },
        {
          icon: Sparkles,
          title: "Best practices",
          body: "How well-run teams set up their desk",
          href: "/docs/best-practices",
        },
        {
          icon: CircleHelp,
          title: "Glossary",
          body: "What SLA, macro, segment and the rest mean",
          href: "/docs/glossary",
        },
      ],
    },
    {
      heading: "Build",
      items: [
        {
          icon: TerminalSquare,
          title: "API reference",
          body: "Every route, with request and response shapes",
          href: "/docs/api",
        },
        ...(swagger
          ? [
              {
                icon: Braces,
                title: "Swagger UI",
                body: "Try the API live in your browser",
                href: swagger,
              },
            ]
          : []),
        {
          icon: Rocket,
          title: "Developer quickstart",
          body: "Sign in and make your first call",
          href: "/docs/developers/quickstart",
        },
        {
          icon: KeyRound,
          title: "Authentication",
          body: "API keys, tokens and sign-in",
          href: "/docs/developers/authentication",
        },
        {
          icon: Webhook,
          title: "Webhooks",
          body: "Send events to your own systems",
          href: "/docs/developers/webhooks-guide",
        },
      ],
    },
    {
      heading: "Stay current",
      items: [
        {
          icon: History,
          title: "Changelog",
          body: "Versioning and what changed in the API",
          href: "/docs/developers/versioning-and-changelog",
        },
        {
          icon: LifeBuoy,
          title: "Errors and limits",
          body: "Status codes, rate limits and retries",
          href: "/docs/developers/errors-and-limits",
        },
      ],
    },
  ];
}

/**
 * A wide "Resources" menu: docs, API and Swagger UI links in columns. The links are plain
 * anchors because the docs are static files served from /docs, outside the Next router.
 */
export function ResourcesMenu({
  label = "Resources",
  newTab = false,
  align = "right",
  className,
  triggerClassName,
}: {
  label?: string;
  newTab?: boolean;
  align?: "left" | "right";
  className?: string;
  triggerClassName?: string;
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);
  const id = React.useId();

  React.useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      ref.current?.querySelector<HTMLElement>("button")?.focus();
    };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  const cols = columns();

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-medium text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground",
          open && "bg-surface-2 text-foreground",
          triggerClassName,
        )}
      >
        {label}
        <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div
          id={id}
          className={cn(
            "absolute top-[calc(100%+8px)] z-50 w-[min(52rem,calc(100vw-1.5rem))] animate-rise overflow-y-auto rounded-2xl border border-border bg-surface p-5 shadow-pop max-sm:fixed max-sm:inset-x-3 max-sm:top-16 max-sm:max-h-[calc(100dvh-5rem)] sm:max-h-[min(36rem,calc(100dvh-5rem))]",
            align === "right" ? "sm:right-0" : "sm:left-0",
            className,
          )}
        >
          <div className="grid gap-x-8 gap-y-6 sm:grid-cols-3">
            {cols.map((col) => (
              <div key={col.heading}>
                <p className="mb-2 px-2 text-[11px] font-semibold tracking-wider text-subtle-foreground uppercase">
                  {col.heading}
                </p>
                <ul className="flex flex-col">
                  {col.items.map((it) => (
                    <li key={it.href}>
                      <a
                        href={it.href}
                        onClick={() => setOpen(false)}
                        {...(newTab || /^https?:/.test(it.href)
                          ? { target: "_blank", rel: "noopener" }
                          : {})}
                        className="group flex gap-3 rounded-lg p-2 transition-colors hover:bg-surface-2 focus-visible:bg-surface-2"
                      >
                        <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg border border-border bg-background text-muted-foreground group-hover:text-primary">
                          <it.icon className="size-4" aria-hidden />
                        </span>
                        <span className="min-w-0">
                          <span className="block text-sm font-semibold text-foreground">
                            {it.title}
                          </span>
                          <span className="block text-xs leading-snug text-muted-foreground">
                            {it.body}
                          </span>
                        </span>
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

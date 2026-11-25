import { Sparkles } from "lucide-react";
import type * as React from "react";

import { cn } from "@/lib/utils";

/** The sparkle mark used wherever Cevanos AI is acting. */
export function AiMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid size-6 shrink-0 place-items-center rounded-lg bg-ai-gradient text-white shadow-[0_4px_14px_-4px_var(--ai-from)]",
        className,
      )}
    >
      <Sparkles className="size-3.5" />
    </span>
  );
}

/** Small "AI" pill: marks generated or AI-assisted content. */
export function AiBadge({
  children = "AI",
  className,
}: {
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "ai-frame inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold",
        className,
      )}
    >
      <Sparkles className="size-3 text-ai-via" aria-hidden />
      <span className="text-ai-gradient">{children}</span>
    </span>
  );
}

/** A panel framed as AI output (suggestions, summaries, drafts). */
export function AiPanel({
  title,
  action,
  children,
  className,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("ai-frame rounded-xl p-4 shadow-card", className)}>
      <header className="mb-2 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-[13px] font-semibold">
          <AiMark className="size-5 rounded-md" />
          {title}
        </h3>
        {action}
      </header>
      <div className="text-sm text-foreground-2">{children}</div>
    </section>
  );
}

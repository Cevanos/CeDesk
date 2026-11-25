import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The one page title block used at the top of every signed-in page: a card with a soft brand
 * glow, an icon chip, the `<h1>`, a one-line description and an optional actions area.
 * On a phone the icon, title and actions stack; from `sm` up the actions sit on the right.
 */
export function PageHeader({
  icon: Icon,
  title,
  description,
  badge,
  actions,
  children,
  as: Tag = "header",
  className,
}: {
  icon?: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  /** Small chip next to the title, e.g. <AiBadge>. */
  badge?: ReactNode;
  /** Buttons / controls aligned to the right (wrap below the title on small screens). */
  actions?: ReactNode;
  /** Extra content under the title row (filters, tabs). */
  children?: ReactNode;
  as?: "header" | "div";
  className?: string;
}) {
  return (
    <Tag
      className={cn(
        "relative overflow-hidden rounded-2xl border border-border bg-surface p-4 shadow-card sm:p-6",
        className,
      )}
    >
      <span
        aria-hidden
        className="pointer-events-none absolute -top-24 -right-16 size-60 rounded-full bg-ai-gradient opacity-15 blur-3xl"
      />
      <div className="relative flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
        <div className="flex min-w-0 flex-1 basis-64 items-center gap-3 sm:gap-4">
          {Icon && (
            <span className="grid size-11 flex-none place-items-center rounded-2xl bg-ai-gradient text-white shadow-[0_8px_22px_-8px_var(--ai-from)] sm:size-12">
              <Icon className="size-5 sm:size-6" aria-hidden />
            </span>
          )}
          <div className="min-w-0">
            <h1 className="flex flex-wrap items-center gap-x-2 gap-y-1 text-2xl font-semibold tracking-tight text-balance">
              {title}
              {badge}
            </h1>
            {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
          </div>
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children && <div className="relative mt-4">{children}</div>}
    </Tag>
  );
}

/** Standard outer wrapper for a signed-in page, so padding is identical everywhere. */
export function PageContainer({
  size = "default",
  className,
  children,
}: {
  size?: "narrow" | "default" | "wide";
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "mx-auto w-full px-3 py-5 sm:px-6 sm:py-8",
        size === "narrow" && "max-w-3xl",
        size === "default" && "max-w-6xl",
        size === "wide" && "max-w-[1600px] lg:px-8",
        className,
      )}
    >
      {children}
    </div>
  );
}

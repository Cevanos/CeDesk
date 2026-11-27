import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The band that opens a page section: a tinted, bordered strip with a brand accent edge, a solid
 * icon chip, the title, an optional one-line description and an optional action on the right.
 * Use it on every <section> so the sections of a page are easy to tell apart at a glance.
 */
export function SectionHeader({
  icon: Icon,
  title,
  description,
  aside,
  id,
  className,
}: {
  icon?: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  aside?: ReactNode;
  id?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "relative mb-3 flex flex-wrap items-center gap-3 overflow-hidden rounded-xl border border-primary/20 bg-linear-to-r from-primary-soft via-primary-soft/40 to-surface py-3 pr-3 pl-5 shadow-card",
        className,
      )}
    >
      <span aria-hidden className="absolute inset-y-0 left-0 w-1.5 bg-ai-gradient" />
      {Icon && (
        <span className="grid size-10 flex-none place-items-center rounded-xl bg-primary text-primary-foreground shadow-[0_6px_16px_-6px_var(--primary)]">
          <Icon className="size-5" aria-hidden />
        </span>
      )}
      <div className="min-w-0 flex-1">
        <h2 id={id} className="text-[17px] leading-tight font-semibold tracking-tight">
          {title}
        </h2>
        {description && <p className="mt-0.5 text-[13px] text-muted-foreground">{description}</p>}
      </div>
      {aside}
    </div>
  );
}

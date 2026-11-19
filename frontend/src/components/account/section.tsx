"use client";

import { Card } from "@/components/ui/primitives";

export function Section({
  icon,
  title,
  description,
  aside,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description?: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card className="p-5 sm:p-6">
      <div className="flex items-start gap-3">
        <span className="grid size-10 flex-none place-items-center rounded-xl bg-primary-soft text-primary-ink">
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold">{title}</h2>
          {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
        </div>
        {aside}
      </div>
      <div className="mt-5">{children}</div>
    </Card>
  );
}

/* ---------------------------------------------------------------- devices */

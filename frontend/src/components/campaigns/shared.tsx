"use client";

import { Ban, Megaphone } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Badge, Card, EmptyState, Skeleton } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { ApiError } from "@/lib/api/client";
import type { CampaignState } from "@/lib/api/types";
import { canManageCampaigns } from "@/lib/permissions";
import { cn } from "@/lib/utils";

/** Only an Admin (the `manage_campaigns` action) may open these pages; the API says 403 to everyone else. */
export function CampaignsGate({ children }: { children: React.ReactNode }) {
  const { user, isPending } = useCurrentUser();
  if (isPending) return <Skeleton className="m-6 h-40" />;
  if (!canManageCampaigns(user)) {
    return (
      <Card className="m-6">
        <EmptyState icon={<Ban className="size-6" />} title="Campaigns are for Admins">
          Ask an Admin of your workspace if you need to send or review a campaign.
        </EmptyState>
      </Card>
    );
  }
  return <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">{children}</div>;
}

export function CampaignTabs() {
  const path = usePathname();
  const tabs = [
    { href: "/campaigns", label: "Campaigns", active: !path.startsWith("/campaigns/suppressions") },
    {
      href: "/campaigns/suppressions",
      label: "Suppression list",
      active: path.startsWith("/campaigns/suppressions"),
    },
  ];
  return (
    <div className="mb-5 flex flex-wrap items-center gap-2">
      <span className="grid size-9 place-items-center rounded-xl bg-ai-gradient text-white shadow-[0_6px_16px_-8px_var(--ai-from)]">
        <Megaphone className="size-4.5" aria-hidden />
      </span>
      <h1 className="mr-3 text-xl font-semibold tracking-tight">Campaigns</h1>
      <nav aria-label="Campaigns" className="flex flex-wrap gap-1">
        {tabs.map((t) => (
          <Link
            key={t.href}
            href={t.href}
            aria-current={t.active ? "page" : undefined}
            className={cn(
              "rounded-lg px-3 py-1.5 text-[13.5px] font-medium transition-colors",
              t.active
                ? "bg-primary/10 text-primary-ink"
                : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
            )}
          >
            {t.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}

const STATE: Record<CampaignState, { label: string; tone: Parameters<typeof Badge>[0]["tone"] }> = {
  draft: { label: "Draft", tone: "neutral" },
  scheduled: { label: "Scheduled", tone: "info" },
  sending: { label: "Sending", tone: "info" },
  paused: { label: "Paused", tone: "warning" },
  sent: { label: "Sent", tone: "success" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  failed: { label: "Failed", tone: "danger" },
};

export function StateBadge({ state }: { state: CampaignState }) {
  const s = STATE[state] ?? { label: state, tone: "neutral" as const };
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

export const CAMPAIGN_STATES: CampaignState[] = [
  "draft",
  "scheduled",
  "sending",
  "paused",
  "sent",
  "cancelled",
  "failed",
];

export function ProgressBar({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      className="h-1.5 w-full overflow-hidden rounded-full bg-surface-3"
    >
      <div
        className="h-full rounded-full bg-ai-gradient transition-all"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

/** "12.5%" of `part` in `whole`, or an em dash when there is nothing to divide by. */
export function percent(part: number, whole: number): string {
  if (!whole) return "—";
  const p = (part / whole) * 100;
  return `${p < 10 ? p.toFixed(1) : Math.round(p)}%`;
}

/** The server writes `message` for the Admin: show it as it is. */
export function errorText(e: unknown): string {
  return e instanceof ApiError
    ? e.message || e.userMessage
    : "Something went wrong. Please try again.";
}

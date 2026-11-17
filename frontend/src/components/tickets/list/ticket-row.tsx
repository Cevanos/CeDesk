import {
  CircleDashed,
  Globe,
  Mail,
  MessageCircle,
  MessageSquare,
  Tag,
  UserRound,
  UsersRound,
} from "lucide-react";
import Link from "next/link";

import {
  AiBadge,
  PriorityBadge,
  StatusBadge,
  VipBadge,
  languageName,
} from "@/components/tickets/badges";
import { SlaPill } from "@/components/tickets/sla";
import { Badge } from "@/components/ui/primitives";
import type { Priority, TicketSummary } from "@/lib/api/types";
import { cn, initials, timeAgo } from "@/lib/utils";

/** Left edge colour that makes priority scannable down a long list. */
const PRIORITY_BAR: Record<Priority, string> = {
  urgent: "bg-danger",
  high: "bg-warning",
  medium: "bg-info",
  low: "bg-border-strong",
};

function ChannelIcon({ channel, className }: { channel: string | null; className?: string }) {
  const c = (channel ?? "").toLowerCase();
  const Icon = c.includes("mail")
    ? Mail
    : c.includes("whatsapp") || c.includes("sms") || c.includes("messag")
      ? MessageSquare
      : c.includes("chat")
        ? MessageCircle
        : Globe;
  return <Icon className={className} aria-hidden />;
}

function Avatar({
  name,
  tone = "brand",
  className,
}: {
  name?: string | null;
  tone?: "brand" | "muted";
  className?: string;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid size-9 flex-none place-items-center rounded-full text-[12px] font-semibold ring-1 ring-inset",
        tone === "brand"
          ? "bg-primary-soft text-primary-ink ring-primary/15"
          : "bg-surface-2 text-muted-foreground ring-border",
        className,
      )}
    >
      {initials(name)}
    </span>
  );
}

export function TicketRow({ t, showStatus }: { t: TicketSummary; showStatus: boolean }) {
  const tags = t.tags ?? [];
  return (
    <Link
      href={`/tickets/${t.id}`}
      className="group relative block py-3.5 pr-3 pl-4 outline-offset-[-2px] transition-colors hover:bg-primary-soft/40 focus-visible:bg-primary-soft/40 sm:py-4 sm:pr-5 sm:pl-5"
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-0 left-0 w-1 transition-all group-hover:w-1.5",
          PRIORITY_BAR[t.priority],
        )}
      />

      <div className="flex items-start gap-3 sm:gap-4">
        <Avatar name={t.contact?.name} className="mt-0.5 hidden sm:grid" />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-mono text-xs font-medium text-muted-foreground">{t.label}</span>
            <PriorityBadge priority={t.priority} />
            {showStatus && <StatusBadge status={t.status} />}
            {t.contact?.vip && <VipBadge />}
            {t.source && <AiBadge source={t.source} />}
            {t.accepted === false && <Badge tone="warning">Waiting to accept</Badge>}
            {t.parent_id && (
              <Badge tone="info" title={`Child of ${t.parent_id}`}>
                Child ticket
              </Badge>
            )}
            {(t.child_count ?? 0) > 0 && (
              <Badge tone="info">
                {t.child_count} {t.child_count === 1 ? "child" : "children"}
              </Badge>
            )}
            {t.merged_into_id && (
              <Badge tone="warning" title={`Merged into ${t.merged_into_id}`}>
                Merged
              </Badge>
            )}
            {(t.jira_keys ?? []).length > 0 && (
              <Badge title={(t.jira_keys ?? []).join(", ")}>
                Jira {t.jira_keys![0]}
                {t.jira_keys!.length > 1 ? ` +${t.jira_keys!.length - 1}` : ""}
              </Badge>
            )}
            {t.status !== "resolved" && <SlaPill sla={t.sla} compact />}
          </div>

          <div className="mt-1.5 line-clamp-2 text-[15px] leading-snug font-semibold text-foreground group-hover:text-primary-ink sm:truncate sm:text-base">
            {t.subject}
          </div>

          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5 font-medium text-foreground-2">
              <Avatar name={t.contact?.name} className="size-5 text-[9px] sm:hidden" />
              {t.contact?.name}
            </span>
            {t.channel && (
              <span className="inline-flex items-center gap-1 capitalize">
                <ChannelIcon channel={t.channel} className="size-3.5" />
                {t.channel}
              </span>
            )}
            {t.type_name && <span className="hidden sm:inline">{t.type_name}</span>}
            {t.team_name && (
              <span className="inline-flex items-center gap-1">
                <UsersRound className="size-3.5" aria-hidden />
                {t.team_name}
              </span>
            )}
            {tags.slice(0, 2).map((g) => (
              <span
                key={g}
                className="hidden items-center gap-0.5 rounded-md bg-surface-2 px-1.5 py-0.5 sm:inline-flex"
              >
                <Tag className="size-3" aria-hidden />
                {g}
              </span>
            ))}
            {tags.length > 2 && <span className="hidden sm:inline">+{tags.length - 2}</span>}
            {t.topic && <Badge tone="primary">{t.topic}</Badge>}
            {t.language && <Badge title={t.language}>{languageName(t.language)}</Badge>}
            {t.source === "demo" && <Badge tone="info">Demo</Badge>}
            {t.sentiment && (
              <Badge className="hidden capitalize sm:inline-flex">{t.sentiment}</Badge>
            )}
          </div>
        </div>

        <div className="flex flex-none flex-col items-end gap-2">
          <time
            className="text-xs whitespace-nowrap text-subtle-foreground"
            dateTime={t.updated_at}
          >
            {timeAgo(t.updated_at)}
          </time>
          {t.assignee_name ? (
            <span
              className="inline-flex items-center gap-2"
              title={`Assigned to ${t.assignee_name}`}
            >
              <span className="hidden max-w-[7rem] truncate text-xs font-medium text-foreground-2 md:inline">
                {t.assignee_name}
              </span>
              <Avatar name={t.assignee_name} className="size-7 text-[10px]" />
            </span>
          ) : (
            <span
              className="inline-flex items-center gap-1.5 rounded-full border border-dashed border-border-strong px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
              title="Unassigned"
            >
              <CircleDashed className="size-3" aria-hidden />
              <span className="hidden md:inline">Unassigned</span>
              <UserRound className="size-3 md:hidden" aria-hidden />
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}

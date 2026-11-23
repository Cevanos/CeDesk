"use client";

import {
  CheckCircle2,
  GitMerge,
  MessageSquare,
  Plus,
  StickyNote,
  Star,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { InfiniteScroll } from "@/components/ui/infinite-scroll";
import { Card, EmptyState, Skeleton } from "@/components/ui/primitives";
import { LoadError } from "@/components/workspace/list-shared";
import { useTimeline } from "@/hooks/use-contacts";
import type { TimelineItem, TimelineKind } from "@/lib/api/contacts-types";
import { cn, formatDateTime } from "@/lib/utils";

const KINDS: { kind: TimelineKind; label: string; icon: LucideIcon; tone: string }[] = [
  {
    kind: "ticket_created",
    label: "Tickets created",
    icon: Plus,
    tone: "bg-primary-soft text-primary-ink",
  },
  {
    kind: "ticket_resolved",
    label: "Resolved",
    icon: CheckCircle2,
    tone: "bg-success-soft text-success",
  },
  { kind: "message", label: "Messages", icon: MessageSquare, tone: "bg-info-soft text-info" },
  { kind: "csat", label: "Ratings", icon: Star, tone: "bg-amber-100 text-amber-700" },
  { kind: "note", label: "Notes", icon: StickyNote, tone: "bg-warning-soft text-warning" },
  { kind: "merge", label: "Merges", icon: GitMerge, tone: "bg-surface-3 text-muted-foreground" },
];

const ticketLabel = (i: TimelineItem) =>
  i.ticket_number != null ? `T-${i.ticket_number}` : "Ticket";

function Stars({ score }: { score: number }) {
  const n = Math.max(0, Math.min(5, Math.round(score)));
  return (
    <span aria-label={`${n} out of 5`} className="text-amber-500">
      {"★".repeat(n)}
      <span className="text-subtle-foreground">{"★".repeat(5 - n)}</span>
    </span>
  );
}

function Body({ item }: { item: TimelineItem }) {
  switch (item.kind) {
    case "ticket_created":
      return (
        <p className="text-sm">
          Ticket <strong>{ticketLabel(item)}</strong> created
          {item.ticket_subject && <> · {item.ticket_subject}</>}
          {item.detail && <span className="text-muted-foreground"> ({item.detail})</span>}
        </p>
      );
    case "ticket_resolved":
      return (
        <p className="text-sm">
          <strong>{ticketLabel(item)}</strong> resolved
        </p>
      );
    case "message":
      return (
        <p
          className={cn(
            "line-clamp-2 text-sm break-words",
            item.detail === "customer" ? "text-foreground" : "text-muted-foreground",
          )}
        >
          <span className="font-medium">{item.author ?? "Someone"}:</span> {item.text}
        </p>
      );
    case "csat":
      return (
        <div className="text-sm">
          Rated <strong>{ticketLabel(item)}</strong>{" "}
          {item.score != null && <Stars score={item.score} />}
          {item.text && <p className="mt-0.5 text-muted-foreground">{item.text}</p>}
        </div>
      );
    case "note":
      return (
        <p className="text-sm break-words">
          <span className="font-medium">{item.author ?? "Someone"}</span> added a note: {item.text}
        </p>
      );
    case "merge":
      return (
        <p className="text-sm">
          <span className="font-medium">{item.author ?? "Someone"}</span>{" "}
          {item.text ? (
            <>
              merged {item.text}
              {item.detail && <> ({item.detail})</>} into this customer
            </>
          ) : (
            "merged a customer"
          )}
        </p>
      );
  }
}

/** The customer's activity, newest first. Chips narrow it to some kinds. */
export function Timeline({ contactId }: { contactId: string }) {
  const [kinds, setKinds] = useState<TimelineKind[]>([]);
  const q = useTimeline(contactId, kinds);
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Show">
        {KINDS.map((k) => {
          const on = kinds.includes(k.kind);
          return (
            <button
              key={k.kind}
              type="button"
              aria-pressed={on}
              onClick={() =>
                setKinds((cur) => (on ? cur.filter((x) => x !== k.kind) : [...cur, k.kind]))
              }
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                on
                  ? "border-primary bg-primary-soft text-primary-ink"
                  : "border-border text-muted-foreground hover:bg-surface-2",
              )}
            >
              {k.label}
            </button>
          );
        })}
      </div>

      {q.isPending ? (
        <Skeleton className="h-40 rounded-2xl" />
      ) : q.isError ? (
        <LoadError error={q.error} />
      ) : items.length === 0 ? (
        <Card className="rounded-2xl">
          <EmptyState title="No activity yet">Nothing matches this view.</EmptyState>
        </Card>
      ) : (
        <Card className="rounded-2xl p-4 sm:p-5">
          <ol className="space-y-4">
            {items.map((i) => {
              const meta = KINDS.find((k) => k.kind === i.kind) ?? KINDS[0]!;
              const Icon = meta.icon;
              const inner = (
                <>
                  <span
                    aria-hidden
                    className={cn(
                      "grid size-8 flex-none place-items-center rounded-full",
                      meta.tone,
                    )}
                  >
                    <Icon className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <Body item={i} />
                    <p className="mt-0.5 text-xs text-muted-foreground">{formatDateTime(i.at)}</p>
                  </div>
                </>
              );
              return (
                <li key={`${i.kind}-${i.id}`}>
                  {i.ticket_id && (i.kind === "message" || i.kind.startsWith("ticket")) ? (
                    <Link
                      href={`/tickets/${i.ticket_id}`}
                      className="-m-1.5 flex gap-3 rounded-lg p-1.5 hover:bg-surface-2"
                    >
                      {inner}
                    </Link>
                  ) : (
                    <div className="flex gap-3">{inner}</div>
                  )}
                </li>
              );
            })}
          </ol>
          <InfiniteScroll query={q} shown={items.length} noun="events" className="mt-2" />
        </Card>
      )}
    </div>
  );
}

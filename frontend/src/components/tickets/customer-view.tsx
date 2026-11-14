"use client";

import {
  ArrowLeft,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  Clock,
  Inbox,
  Mail,
  Smile,
  Star,
  Ticket,
} from "lucide-react";
import Link from "next/link";

import { PriorityBadge, StatusBadge } from "@/components/tickets/badges";
import { Button, Card, EmptyState, Skeleton } from "@/components/ui/primitives";
import { useContactArchive } from "@/hooks/use-archive";
import { useCurrentUser } from "@/hooks/use-auth";
import { useContactTickets, useUpdateContact } from "@/hooks/use-tickets";
import { ApiError } from "@/lib/api/client";
import { cn, formatDateTime, initials, timeAgo } from "@/lib/utils";

function Stat({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: typeof Ticket;
  label: string;
  value: React.ReactNode;
  tone: string;
}) {
  return (
    <Card className="group relative overflow-hidden p-4 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-pop">
      <div className="flex items-center gap-3">
        <span
          className={cn(
            "grid size-10 shrink-0 place-items-center rounded-xl ring-1 ring-black/5 ring-inset",
            tone,
          )}
        >
          <Icon className="size-[18px]" />
        </span>
        <div className="min-w-0">
          <p className="text-[11px] font-medium tracking-wider text-subtle-foreground uppercase">
            {label}
          </p>
          <p className="truncate text-lg leading-tight font-semibold tracking-tight">{value}</p>
        </div>
      </div>
    </Card>
  );
}

export function CustomerView({ id }: { id: string }) {
  const { perms } = useCurrentUser();
  const q = useContactTickets(id);
  const update = useUpdateContact(id);
  const archived = useContactArchive(id);

  if (q.isPending) {
    return (
      <div className="mx-auto max-w-4xl space-y-4 p-6">
        <Skeleton className="h-44 rounded-2xl" />
        <div className="grid gap-3 sm:grid-cols-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
        </div>
        <Skeleton className="h-60" />
      </div>
    );
  }
  if (q.isError) {
    return (
      <EmptyState title="Customer not found">
        {q.error instanceof ApiError ? q.error.userMessage : "Please try again."}
      </EmptyState>
    );
  }

  const { contact, items } = q.data;
  const openCount = items.filter((t) => t.status !== "resolved").length;
  const resolvedCount = items.length - openCount;

  return (
    <div className="mx-auto w-full max-w-4xl animate-rise px-3 py-5 sm:px-6 sm:py-7">
      <Link
        href="/tickets"
        className="mb-4 inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-sm text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> Tickets
      </Link>

      <Card className="relative overflow-hidden rounded-2xl border-0 shadow-pop ring-1 ring-border">
        <div className="relative h-24 bg-gradient-to-br from-[#5b45e8] via-[#7762fb] to-[#a78bfa] sm:h-28">
          <div className="pointer-events-none absolute -top-10 -right-8 size-48 rounded-full bg-white/15 blur-2xl" />
          <div className="pointer-events-none absolute -bottom-16 left-1/3 size-40 rounded-full bg-[#2b6cf5]/30 blur-3xl" />
          <div
            className="pointer-events-none absolute inset-0 opacity-[0.12]"
            style={{
              backgroundImage: "radial-gradient(circle at 1px 1px, #fff 1px, transparent 0)",
              backgroundSize: "18px 18px",
            }}
          />
        </div>
        <div className="flex flex-wrap items-end gap-4 px-4 pb-5 sm:px-6">
          <div
            className={cn(
              "-mt-10 grid size-20 shrink-0 place-items-center rounded-2xl bg-surface text-2xl font-semibold tracking-tight text-primary-ink shadow-pop ring-4 ring-surface sm:-mt-12 sm:size-24 sm:text-3xl",
              contact.vip && "bg-gradient-to-br from-amber-200 to-amber-400 text-amber-900",
            )}
            aria-hidden
          >
            {initials(contact.name)}
          </div>
          <div className="min-w-0 flex-1 pt-3">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate text-2xl font-semibold tracking-tight">{contact.name}</h1>
              {contact.vip && (
                <span className="inline-flex items-center gap-1 rounded-full bg-gradient-to-r from-amber-100 to-amber-200 px-2.5 py-0.5 text-xs font-semibold text-amber-900 ring-1 ring-amber-300/60">
                  <Star className="size-3 fill-current" /> VIP
                </span>
              )}
            </div>
            <a
              href={`mailto:${contact.email}`}
              className="mt-0.5 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-primary-ink"
            >
              <Mail className="size-3.5" /> {contact.email}
            </a>
          </div>
          {perms.editTicket && (
            <Button
              variant={contact.vip ? "primary" : "outline"}
              loading={update.isPending}
              onClick={() => update.mutate({ vip: !contact.vip })}
              aria-pressed={!!contact.vip}
            >
              <Star className={contact.vip ? "size-4 fill-current" : "size-4"} />{" "}
              {contact.vip ? "VIP customer" : "Mark as VIP"}
            </Button>
          )}
        </div>
      </Card>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Stat
          icon={Ticket}
          label="Total tickets"
          value={items.length}
          tone="bg-primary-soft text-primary-ink"
        />
        <Stat icon={Inbox} label="Active" value={openCount} tone="bg-info-soft text-info" />
        <Stat
          icon={CheckCircle2}
          label="Resolved"
          value={resolvedCount}
          tone="bg-success-soft text-success"
        />
        <Stat
          icon={Smile}
          label="Satisfaction"
          value={
            contact.csat_count && contact.csat_average != null ? (
              <>
                {contact.csat_average.toFixed(1)} ★{" "}
                <span className="text-xs font-normal text-muted-foreground">
                  · {contact.csat_count} {contact.csat_count === 1 ? "rating" : "ratings"}
                </span>
              </>
            ) : (
              "No ratings"
            )
          }
          tone="bg-amber-100 text-amber-700"
        />
        <Stat
          icon={CalendarDays}
          label="Customer since"
          value={
            contact.customer_since ? formatDateTime(contact.customer_since).split(",")[0] : "—"
          }
          tone="bg-warning-soft text-warning"
        />
      </div>

      <div className="mt-8 mb-3 flex items-center gap-2">
        <h2 className="text-base font-semibold tracking-tight">All tickets</h2>
        <span className="rounded-full bg-surface-3 px-2 py-0.5 text-xs font-medium text-muted-foreground">
          {items.length}
        </span>
      </div>
      <Card className="overflow-hidden rounded-2xl">
        <ul className="divide-y divide-border">
          {items.map((t) => (
            <li key={t.id}>
              <Link
                href={`/tickets/${t.id}`}
                className="group relative flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3.5 transition-colors before:absolute before:inset-y-2 before:left-0 before:w-[3px] before:rounded-r-full before:bg-primary before:opacity-0 before:transition-opacity hover:bg-primary-soft/40 hover:before:opacity-100 sm:px-5"
              >
                <span className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                  {t.label}
                </span>
                <span className="min-w-0 flex-1 basis-48 truncate font-medium">{t.subject}</span>
                <PriorityBadge priority={t.priority} />
                <StatusBadge status={t.status} />
                <span className="inline-flex items-center gap-1 text-xs text-subtle-foreground">
                  <Clock className="size-3" /> {timeAgo(t.updated_at)}
                </span>
                <ChevronRight className="size-4 text-subtle-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary-ink" />
              </Link>
            </li>
          ))}
          {items.length === 0 && (
            <li className="flex flex-col items-center gap-2 p-10 text-center text-sm text-muted-foreground">
              <Inbox className="size-8 text-subtle-foreground" />
              No tickets yet.
            </li>
          )}
        </ul>
      </Card>

      {archived.data && archived.data.length > 0 && (
        <>
          <div className="mt-8 mb-3 flex items-center gap-2">
            <h2 className="text-base font-semibold tracking-tight">Archived tickets</h2>
            <span className="rounded-full bg-surface-3 px-2 py-0.5 text-xs font-medium text-muted-foreground">
              {archived.data.length}
            </span>
          </div>
          <Card className="overflow-hidden rounded-2xl">
            <ul className="divide-y divide-border">
              {archived.data.map((t) => (
                <li key={t.id}>
                  <Link
                    href={`/archive/${encodeURIComponent(t.id)}`}
                    className="flex items-center gap-3 px-4 py-3 hover:bg-surface-2 sm:px-5"
                  >
                    <span className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                      {t.label ?? (t.number != null ? `T-${t.number}` : t.id.slice(0, 8))}
                    </span>
                    <span className="min-w-0 flex-1 truncate font-medium">{t.subject}</span>
                    <span className="text-xs text-muted-foreground">Archived</span>
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        </>
      )}
    </div>
  );
}

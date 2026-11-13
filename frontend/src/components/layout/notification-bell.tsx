"use client";

import { AlarmClock, AtSign, Bell, CheckCheck, UserPlus } from "lucide-react";
import { useRouter } from "next/navigation";

import { MenuItem, Popover } from "@/components/ui/overlay";
import { Skeleton } from "@/components/ui/primitives";
import { useMarkAllRead, useMarkRead, useNotifications } from "@/hooks/use-workspace";
import { cn, timeAgo } from "@/lib/utils";

export function NotificationBell() {
  const router = useRouter();
  const { data, isLoading } = useNotifications();
  const markRead = useMarkRead();
  const markAll = useMarkAllRead();
  const unread = data?.unread ?? 0;

  return (
    <Popover
      className="w-[min(22rem,calc(100vw-2rem))] p-0 max-sm:fixed max-sm:inset-x-3 max-sm:top-16 max-sm:w-auto"
      trigger={({ props }) => (
        <button
          {...props}
          aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`}
          className="relative grid size-9 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"
        >
          <Bell className="size-[18px]" />
          {unread > 0 && (
            <span className="absolute -top-0.5 -right-0.5 grid min-w-[18px] place-items-center rounded-full bg-danger px-1 text-[10px] font-bold text-primary-foreground ring-2 ring-surface">
              {unread > 99 ? "99+" : unread}
            </span>
          )}
        </button>
      )}
    >
      {(close) => (
        <div>
          <div className="flex items-center justify-between border-b border-border px-3.5 py-2.5">
            <span className="text-sm font-semibold">Notifications</span>
            <button
              disabled={!unread || markAll.isPending}
              onClick={() => markAll.mutate()}
              className="inline-flex items-center gap-1 text-xs font-medium text-primary-ink hover:underline disabled:opacity-40"
            >
              <CheckCheck className="size-3.5" /> Mark all read
            </button>
          </div>
          <div className="max-h-96 overflow-y-auto p-1.5">
            {isLoading ? (
              <div className="space-y-2 p-2">
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
              </div>
            ) : !data?.items.length ? (
              <p className="px-3 py-8 text-center text-sm text-muted-foreground">
                You are all caught up.
              </p>
            ) : (
              data.items.map((n) => (
                <MenuItem
                  key={n.id}
                  className={cn("items-start", !n.read && "bg-primary-soft/60")}
                  onClick={() => {
                    if (!n.read) markRead.mutate(n.id);
                    close();
                    if (n.ticket_id) router.push(`/tickets/${n.ticket_id}`);
                  }}
                >
                  <span
                    className={cn(
                      "mt-0.5 grid size-7 flex-none place-items-center rounded-full",
                      n.kind === "sla_breach"
                        ? "bg-danger-soft text-danger"
                        : "bg-primary-soft text-primary-ink",
                    )}
                  >
                    {n.kind === "sla_breach" ? (
                      <AlarmClock className="size-3.5" aria-label="SLA breached" />
                    ) : n.kind === "mention" ? (
                      <AtSign className="size-3.5" />
                    ) : (
                      <UserPlus className="size-3.5" />
                    )}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-[13px] leading-snug">{n.text}</span>
                    <span className="text-[11px] text-subtle-foreground">
                      {timeAgo(n.created_at)}
                    </span>
                  </span>
                </MenuItem>
              ))
            )}
          </div>
        </div>
      )}
    </Popover>
  );
}

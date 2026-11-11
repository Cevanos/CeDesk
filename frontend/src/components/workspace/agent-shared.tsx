import { Mail, MessageCircle, MessagesSquare } from "lucide-react";
import type * as React from "react";

import { STATUS_META } from "@/components/layout/user-menu";
import type { AgentRow, AgentStatus, ChannelGroup } from "@/lib/api/types";
import { cn, initials } from "@/lib/utils";

export const CHANNELS: { key: ChannelGroup; label: string; icon: React.ElementType }[] = [
  { key: "email", label: "Email", icon: Mail },
  { key: "chat", label: "Chat", icon: MessageCircle },
  { key: "messaging", label: "Messaging", icon: MessagesSquare },
];

const GRADIENTS = [
  "from-[#a99bff] to-[#5b3cf0]",
  "from-[#7fd1ff] to-[#2b6cf5]",
  "from-[#7ee8c0] to-[#0f9d72]",
  "from-[#ffb48a] to-[#e2543a]",
  "from-[#f6a6ff] to-[#a23cf0]",
  "from-[#ffd36e] to-[#e08a00]",
];

export function gradientFor(name: string) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return GRADIENTS[h % GRADIENTS.length];
}

export function Avatar({
  name,
  status,
  size = "md",
}: {
  name: string;
  status: AgentStatus;
  size?: "md" | "xl";
}) {
  const s = STATUS_META[status] ?? STATUS_META.offline;
  return (
    <span
      className={cn(
        "relative grid flex-none place-items-center rounded-full bg-gradient-to-br font-bold text-white shadow-[0_6px_16px_-6px_rgb(60_40_160/0.6)]",
        gradientFor(name),
        size === "xl" ? "size-20 text-2xl ring-4 ring-surface" : "size-11 text-sm",
      )}
    >
      {initials(name)}
      <span
        className={cn(
          "absolute rounded-full ring-2 ring-surface",
          s.dot,
          size === "xl" ? "right-0.5 bottom-0.5 size-4" : "-right-0.5 -bottom-0.5 size-3",
          status === "available" &&
            "after:absolute after:inset-0 after:animate-ping after:rounded-full after:bg-success/50",
        )}
      />
    </span>
  );
}

/** Total tickets an agent may hold, based on their enabled channels. */
export function totalCapacity(
  a: AgentRow,
  defaults?: { cap_email: number; cap_chat: number; cap_messaging: number },
): number | null {
  if (typeof a.capacity === "number") return a.capacity;
  const chans = a.channels ?? [];
  if (!chans.length) return null;
  let total = 0;
  for (const c of chans) {
    const own = a.capacity && typeof a.capacity === "object" ? a.capacity[c] : undefined;
    const def = defaults ? defaults[`cap_${c}` as "cap_email"] : undefined;
    const v = own ?? def;
    if (v == null) return null;
    total += v;
  }
  return total;
}

export function LoadBar({
  open,
  cap,
  className,
}: {
  open: number;
  cap: number | null;
  className?: string;
}) {
  if (!cap) return null;
  const pct = Math.min(100, Math.round((open / cap) * 100));
  const tone = pct >= 90 ? "bg-danger" : pct >= 65 ? "bg-warning" : "bg-primary";
  return (
    <div
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-surface-3", className)}
      role="img"
      aria-label={`${pct}% of capacity in use`}
    >
      <div
        className={cn("h-full rounded-full transition-all duration-500", tone)}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

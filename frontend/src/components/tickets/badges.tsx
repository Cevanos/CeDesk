import { Sparkles, Star } from "lucide-react";

import { Badge } from "@/components/ui/primitives";
import type { Priority, TicketStatus } from "@/lib/api/types";

export const PRIORITY_LABEL: Record<Priority, string> = {
  urgent: "Urgent",
  high: "High",
  medium: "Medium",
  low: "Low",
};
export const STATUS_LABEL: Record<TicketStatus, string> = {
  open: "Open",
  pending: "Pending",
  resolved: "Resolved",
};
export const PRIORITIES: Priority[] = ["urgent", "high", "medium", "low"];
export const STATUSES: TicketStatus[] = ["open", "pending", "resolved"];

const P_TONE = {
  urgent: "solidDanger",
  high: "danger",
  medium: "warning",
  low: "neutral",
} as const;
const S_TONE = { open: "primary", pending: "warning", resolved: "success" } as const;

export function PriorityBadge({ priority }: { priority: Priority }) {
  return <Badge tone={P_TONE[priority]}>{PRIORITY_LABEL[priority]}</Badge>;
}

/** `label` overrides the default text, e.g. the customer portal's "Waiting on you". */
export function StatusBadge({ status, label }: { status: TicketStatus; label?: string | null }) {
  return <Badge tone={S_TONE[status]}>{label || STATUS_LABEL[status]}</Badge>;
}

/** "hi" -> "Hindi". An unknown code is shown as it came, in capitals. */
export function languageName(code: string): string {
  try {
    return new Intl.DisplayNames(["en"], { type: "language" }).of(code) ?? code.toUpperCase();
  } catch {
    return code.toUpperCase();
  }
}

export function VipBadge() {
  return (
    <Badge tone="warning">
      <Star className="size-3 fill-current" /> VIP
    </Badge>
  );
}

const IMPORT_SOURCES = new Set([
  "csv",
  "zendesk",
  "freshdesk",
  "intercom",
  "helpscout",
  "hubspot",
  "zoho",
  "zoho_desk",
]);

export function AiBadge({ source }: { source?: string | null }) {
  const key = source?.toLowerCase().replace(/[\s-]/g, "_");
  if (key && IMPORT_SOURCES.has(key)) {
    const name =
      key === "csv"
        ? "CSV"
        : key === "zoho_desk"
          ? "Zoho Desk"
          : source!.replace(/^./, (c) => c.toUpperCase());
    return <Badge tone="neutral">Imported · {name}</Badge>;
  }
  return (
    <Badge tone="primary">
      <Sparkles className="size-3" /> {source ? `AI · ${source}` : "AI"}
    </Badge>
  );
}

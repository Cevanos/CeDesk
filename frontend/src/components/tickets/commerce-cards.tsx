"use client";

import { ExtraCard, Muted, QueryState } from "@/components/tickets/extras-shared";
import { Badge } from "@/components/ui/primitives";
import { useOrders, usePayments, type CommerceCard } from "@/hooks/use-ticket-extras";
import { formatDateTime } from "@/lib/utils";

type Row = Record<string, unknown>;

const str = (v: unknown) => (typeof v === "string" || typeof v === "number" ? String(v) : "");

function amount(r: Row) {
  const raw = r.total ?? r.amount ?? r.total_price;
  if (raw === undefined || raw === null || typeof raw === "object") return "";
  const cur = str(r.currency);
  return cur ? `${str(raw)} ${cur}` : str(raw);
}

function Items({ data, empty, title }: { data: CommerceCard; empty: string; title: string }) {
  if (!data.available)
    return <Muted>{data.message || `${title} aren't available right now.`}</Muted>;
  if (!data.items.length) return <Muted>{empty}</Muted>;
  return (
    <ul className="space-y-2">
      {data.items.slice(0, 5).map((r, i) => {
        const status = str(r.status ?? r.state);
        const when = str(r.created_at ?? r.date ?? r.placed_at);
        return (
          <li key={str(r.id) || i} className="text-sm">
            <div className="flex items-start gap-2">
              <span className="min-w-0 flex-1 font-medium wrap-break-word">
                {str(r.number ?? r.name ?? r.reference ?? r.id) || title}
              </span>
              {amount(r) && <span className="flex-none tabular-nums">{amount(r)}</span>}
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              {status && <Badge>{status}</Badge>}
              {when && !Number.isNaN(Date.parse(when)) && <span>{formatDateTime(when)}</span>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function OrdersCard({ ticketId }: { ticketId: string }) {
  const q = useOrders(ticketId);
  return (
    <ExtraCard title="Recent orders">
      <QueryState q={q} />
      {q.data && <Items data={q.data} empty="No orders for this customer." title="Orders" />}
    </ExtraCard>
  );
}

export function PaymentsCard({ ticketId }: { ticketId: string }) {
  const q = usePayments(ticketId);
  return (
    <ExtraCard title="Recent payments">
      <QueryState q={q} />
      {q.data && <Items data={q.data} empty="No payments for this customer." title="Payments" />}
    </ExtraCard>
  );
}

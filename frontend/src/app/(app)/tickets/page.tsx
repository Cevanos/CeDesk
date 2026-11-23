import type { Metadata } from "next";
import { Suspense } from "react";

import { TicketListView } from "@/components/tickets/list/ticket-list-view";

export const metadata: Metadata = { title: "Tickets" };

export default function TicketsPage() {
  return (
    <Suspense>
      <TicketListView />
    </Suspense>
  );
}

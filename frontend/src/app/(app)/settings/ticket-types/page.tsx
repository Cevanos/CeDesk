import type { Metadata } from "next";

import { TicketTypesView } from "@/components/workspace/ticket-types-view";

export const metadata: Metadata = { title: "Ticket types" };

export default function Page() {
  return <TicketTypesView />;
}

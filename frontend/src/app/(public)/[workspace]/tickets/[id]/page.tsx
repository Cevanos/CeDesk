import type { Metadata } from "next";

import { PortalTicketPage } from "@/components/public/portal-ticket";

export const metadata: Metadata = { title: "Your ticket" };

export default function Page() {
  return <PortalTicketPage />;
}

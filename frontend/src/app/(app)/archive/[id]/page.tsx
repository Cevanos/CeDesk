import type { Metadata } from "next";

import { ArchivedTicketView } from "@/components/archive/archived-ticket";

export const metadata: Metadata = { title: "Archived ticket" };

export default async function ArchivedTicketPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ArchivedTicketView id={id} />;
}

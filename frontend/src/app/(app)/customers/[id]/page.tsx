import type { Metadata } from "next";

import { CustomerView } from "@/components/tickets/customer-view";

export const metadata: Metadata = { title: "Customer" };

export default async function CustomerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <CustomerView id={id} />;
}

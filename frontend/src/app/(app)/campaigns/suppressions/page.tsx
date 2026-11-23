import type { Metadata } from "next";

import { SuppressionsView } from "@/components/campaigns/suppressions-view";

export const metadata: Metadata = { title: "Suppression list" };

export default function Page() {
  return <SuppressionsView />;
}

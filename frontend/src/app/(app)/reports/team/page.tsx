import type { Metadata } from "next";

import { ReportsView } from "@/components/workspace/reports-view";

export const metadata: Metadata = { title: "Team report" };

export default function Page() {
  return <ReportsView />;
}

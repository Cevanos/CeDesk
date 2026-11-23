import type { Metadata } from "next";

import { DashboardsList } from "@/components/workspace/reports-dashboards";

export const metadata: Metadata = { title: "Dashboards" };

export default function Page() {
  return <DashboardsList />;
}

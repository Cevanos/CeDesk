import type { Metadata } from "next";

import { MetricsTab } from "@/components/workspace/reports-tabs";

export const metadata: Metadata = { title: "More metrics" };

export default function Page() {
  return <MetricsTab />;
}

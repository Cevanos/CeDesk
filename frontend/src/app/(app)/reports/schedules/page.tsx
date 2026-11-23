import type { Metadata } from "next";

import { SchedulesTab } from "@/components/workspace/reports-schedules";

export const metadata: Metadata = { title: "Scheduled reports" };

export default function Page() {
  return <SchedulesTab />;
}

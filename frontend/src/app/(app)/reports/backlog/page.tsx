import type { Metadata } from "next";

import { BacklogTab } from "@/components/workspace/reports-backlog";

export const metadata: Metadata = { title: "Backlog" };

export default function Page() {
  return <BacklogTab />;
}

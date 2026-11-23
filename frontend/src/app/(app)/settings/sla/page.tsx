import type { Metadata } from "next";

import { SlaView } from "@/components/workspace/sla-view";

export const metadata: Metadata = { title: "SLA" };

export default function Page() {
  return <SlaView />;
}

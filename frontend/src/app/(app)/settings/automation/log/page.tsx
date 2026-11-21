import type { Metadata } from "next";
import { Suspense } from "react";

import { Skeleton } from "@/components/ui/primitives";
import { AutomationLogView } from "@/components/workspace/automation-log-view";

export const metadata: Metadata = { title: "Automation log" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-40" />}>
      <AutomationLogView />
    </Suspense>
  );
}

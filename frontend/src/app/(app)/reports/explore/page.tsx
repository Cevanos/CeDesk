import type { Metadata } from "next";
import { Suspense } from "react";

import { TrendsTab } from "@/components/workspace/reports-trends";

export const metadata: Metadata = { title: "Trends" };

export default function Page() {
  return (
    <Suspense>
      <TrendsTab />
    </Suspense>
  );
}

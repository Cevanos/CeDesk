import type { Metadata } from "next";

import { FailedView } from "@/components/workspace/failed-view";

export const metadata: Metadata = { title: "Failed events" };

export default function Page() {
  return <FailedView />;
}

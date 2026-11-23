import type { Metadata } from "next";

import { LiveTab } from "@/components/workspace/reports-live";

export const metadata: Metadata = { title: "Live queue" };

export default function Page() {
  return <LiveTab />;
}

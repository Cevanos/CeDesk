import type { Metadata } from "next";

import { TopicsView } from "@/components/workspace/topics-view";

export const metadata: Metadata = { title: "Topics & redaction" };

export default function Page() {
  return <TopicsView />;
}

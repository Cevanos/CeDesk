import type { Metadata } from "next";

import { AiTab } from "@/components/workspace/reports-tabs";

export const metadata: Metadata = { title: "AI report" };

export default function Page() {
  return <AiTab />;
}

import type { Metadata } from "next";

import { DemoView } from "@/components/workspace/demo-view";

export const metadata: Metadata = { title: "Demo data" };

export default function Page() {
  return <DemoView />;
}

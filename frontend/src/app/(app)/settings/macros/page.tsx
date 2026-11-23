import type { Metadata } from "next";

import { MacrosView } from "@/components/workspace/macros-view";

export const metadata: Metadata = { title: "Macros" };

export default function Page() {
  return <MacrosView />;
}

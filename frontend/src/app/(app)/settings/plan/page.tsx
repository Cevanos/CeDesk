import type { Metadata } from "next";

import { PlanView } from "@/components/workspace/plan-view";

export const metadata: Metadata = { title: "Plan & retention" };

export default function Page() {
  return <PlanView />;
}

import type { Metadata } from "next";

import { TeamsView } from "@/components/workspace/teams-view";

export const metadata: Metadata = { title: "Teams" };

export default function Page() {
  return <TeamsView />;
}

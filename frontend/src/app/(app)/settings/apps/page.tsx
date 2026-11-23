import type { Metadata } from "next";

import { AppsView } from "@/components/workspace/apps-view";

export const metadata: Metadata = { title: "Apps" };

export default function Page() {
  return <AppsView />;
}

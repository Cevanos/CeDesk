import type { Metadata } from "next";

import { HelpCenterView } from "@/components/workspace/help-center-view";

export const metadata: Metadata = { title: "Help center" };

export default function Page() {
  return <HelpCenterView />;
}

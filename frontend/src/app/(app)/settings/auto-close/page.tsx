import type { Metadata } from "next";

import { AutoCloseView } from "@/components/workspace/auto-close-view";

export const metadata: Metadata = { title: "Auto-close" };

export default function Page() {
  return <AutoCloseView />;
}

import type { Metadata } from "next";

import { ReceiptsView } from "@/components/workspace/receipts-view";

export const metadata: Metadata = { title: "Resolution receipts" };

export default function Page() {
  return <ReceiptsView />;
}

import type { Metadata } from "next";

import { CevanosView } from "@/components/workspace/cevanos-view";

export const metadata: Metadata = { title: "Cevanos" };

export default function Page() {
  return <CevanosView />;
}

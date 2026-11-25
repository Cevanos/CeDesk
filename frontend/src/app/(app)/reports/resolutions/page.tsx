import type { Metadata } from "next";

import { ResolutionsTab } from "@/components/workspace/reports-resolutions";

export const metadata: Metadata = { title: "Resolutions" };

export default function Page() {
  return <ResolutionsTab />;
}

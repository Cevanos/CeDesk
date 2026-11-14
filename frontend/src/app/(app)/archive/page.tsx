import type { Metadata } from "next";
import { Suspense } from "react";

import { ArchiveSearchView } from "@/components/archive/archive-search";

export const metadata: Metadata = { title: "Archive" };

export default function ArchivePage() {
  return (
    <Suspense>
      <ArchiveSearchView />
    </Suspense>
  );
}

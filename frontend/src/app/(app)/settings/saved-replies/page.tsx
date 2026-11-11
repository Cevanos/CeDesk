import type { Metadata } from "next";

import { SavedRepliesView } from "@/components/workspace/saved-replies-view";

export const metadata: Metadata = { title: "Saved replies" };

export default function Page() {
  return <SavedRepliesView />;
}

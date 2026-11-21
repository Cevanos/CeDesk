import type { Metadata } from "next";

import { UnsubscribePage } from "@/components/public/unsubscribe-page";

export const metadata: Metadata = { title: "Unsubscribe", robots: { index: false } };

export default function Page() {
  return <UnsubscribePage />;
}

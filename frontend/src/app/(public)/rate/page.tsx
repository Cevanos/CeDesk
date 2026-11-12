import type { Metadata } from "next";

import { CsatPage } from "@/components/public/csat-page";

export const metadata: Metadata = { title: "Rate your support" };

export default function Page() {
  return <CsatPage />;
}

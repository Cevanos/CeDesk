import type { Metadata } from "next";

import { ImportsView } from "@/components/workspace/imports-view";

export const metadata: Metadata = { title: "Imports" };

export default function Page() {
  return <ImportsView />;
}

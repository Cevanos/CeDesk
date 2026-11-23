import type { Metadata } from "next";

import { SandboxView } from "@/components/workspace/sandbox-view";

export const metadata: Metadata = { title: "Sandbox" };

export default function Page() {
  return <SandboxView />;
}

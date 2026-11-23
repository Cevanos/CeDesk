import type { Metadata } from "next";

import { ApiKeysView } from "@/components/workspace/api-keys-view";

export const metadata: Metadata = { title: "API keys" };

export default function Page() {
  return <ApiKeysView />;
}

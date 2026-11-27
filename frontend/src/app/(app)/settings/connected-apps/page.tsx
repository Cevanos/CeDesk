import type { Metadata } from "next";

import { ConnectedAppsCard } from "@/components/account/connected-apps-card";

export const metadata: Metadata = { title: "Connected apps" };

export default function Page() {
  return <ConnectedAppsCard />;
}

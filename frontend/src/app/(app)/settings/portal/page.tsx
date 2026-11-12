import type { Metadata } from "next";

import { PortalSettingsView } from "@/components/workspace/portal-settings-view";

export const metadata: Metadata = { title: "Portal" };

export default function Page() {
  return <PortalSettingsView />;
}

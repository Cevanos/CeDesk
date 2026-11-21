import type { Metadata } from "next";

import { CampaignsView } from "@/components/campaigns/campaigns-view";

export const metadata: Metadata = { title: "Campaigns" };

export default function Page() {
  return <CampaignsView />;
}

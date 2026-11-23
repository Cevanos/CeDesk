import type { Metadata } from "next";

import { CampaignPage } from "@/components/campaigns/campaign-page";

export const metadata: Metadata = { title: "Campaign" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <CampaignPage id={id} />;
}

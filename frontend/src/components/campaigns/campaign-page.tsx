"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { CampaignEditor } from "@/components/campaigns/campaign-editor";
import { CampaignResults } from "@/components/campaigns/campaign-results";
import { CampaignsGate } from "@/components/campaigns/shared";
import { Skeleton } from "@/components/ui/primitives";
import { LoadError } from "@/components/workspace/list-shared";
import { useCampaign } from "@/hooks/use-workspace";

/** A draft opens the editor; anything else opens the results. */
export function CampaignPage({ id }: { id: string }) {
  return (
    <CampaignsGate>
      <Link
        href="/campaigns"
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> All campaigns
      </Link>
      <Body id={id} />
    </CampaignsGate>
  );
}

function Body({ id }: { id: string }) {
  const c = useCampaign(id);
  if (c.isPending) return <Skeleton className="h-64" />;
  if (c.isError) return <LoadError error={c.error} />;
  return c.data.state === "draft" ? (
    // A new key per updated_at would reset typing; the editor owns its text after the first load.
    <CampaignEditor key={c.data.id} campaign={c.data} />
  ) : (
    <CampaignResults campaign={c.data} />
  );
}

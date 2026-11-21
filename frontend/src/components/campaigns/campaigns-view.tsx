"use client";

import { Megaphone, Plus, Settings } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { CampaignSettingsDialog } from "@/components/campaigns/settings-dialog";
import {
  CAMPAIGN_STATES,
  CampaignTabs,
  CampaignsGate,
  ProgressBar,
  StateBadge,
  percent,
} from "@/components/campaigns/shared";
import { Button, Card, EmptyState, Select, Skeleton } from "@/components/ui/primitives";
import { LoadError } from "@/components/workspace/list-shared";
import { useCampaigns, useCreateCampaign } from "@/hooks/use-workspace";
import type { Campaign } from "@/lib/api/types";
import { formatDateTime } from "@/lib/utils";

export function CampaignsView() {
  return (
    <CampaignsGate>
      <CampaignTabs />
      <CampaignList />
    </CampaignsGate>
  );
}

function whenText(c: Campaign): string {
  if (c.state === "scheduled" && c.scheduled_at)
    return `Goes out ${formatDateTime(c.scheduled_at)}`;
  if (c.finished_at) return `Finished ${formatDateTime(c.finished_at)}`;
  if (c.started_at) return `Started ${formatDateTime(c.started_at)}`;
  return `Created ${formatDateTime(c.created_at)}`;
}

function CampaignList() {
  const router = useRouter();
  const [state, setState] = useState("all");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const list = useCampaigns(state);
  const create = useCreateCampaign();
  const rows = list.data?.pages.flatMap((p) => p.campaigns) ?? [];

  const open = (c: Campaign) => router.push(`/campaigns/${encodeURIComponent(c.id)}`);

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select
          aria-label="Filter by state"
          value={state}
          onChange={(e) => setState(e.target.value)}
          className="w-44"
        >
          <option value="all">All states</option>
          {CAMPAIGN_STATES.map((s) => (
            <option key={s} value={s}>
              {s[0]!.toUpperCase() + s.slice(1)}
            </option>
          ))}
        </Select>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" onClick={() => setSettingsOpen(true)}>
            <Settings className="size-4" /> Settings
          </Button>
          <Button
            loading={create.isPending}
            onClick={() =>
              create.mutate("Untitled campaign", {
                onSuccess: (c) => router.push(`/campaigns/${encodeURIComponent(c.id)}`),
              })
            }
          >
            <Plus className="size-4" /> New campaign
          </Button>
        </div>
      </div>

      {list.isPending ? (
        <Skeleton className="h-48" />
      ) : list.isError ? (
        <LoadError error={list.error} />
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState icon={<Megaphone className="size-6" />} title="No campaigns yet">
            {state === "all"
              ? "A campaign sends one email to a segment of your customers. Start with New campaign."
              : "No campaign is in this state."}
          </EmptyState>
        </Card>
      ) : (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[56rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="px-4 py-2.5 font-medium">Name</th>
                <th className="px-3 py-2.5 font-medium">State</th>
                <th className="px-3 py-2.5 font-medium">Segment</th>
                <th className="px-3 py-2.5 font-medium">Sent</th>
                <th className="px-3 py-2.5 font-medium">Opened</th>
                <th className="px-3 py-2.5 font-medium">Unsubscribed</th>
                <th className="px-3 py-2.5 font-medium">When</th>
                <th className="px-3 py-2.5 font-medium">Created by</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((c) => (
                <tr
                  key={c.id}
                  tabIndex={0}
                  onClick={() => open(c)}
                  onKeyDown={(e) => e.key === "Enter" && open(c)}
                  className="cursor-pointer align-top hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none"
                >
                  <td className="max-w-64 px-4 py-3 font-medium">
                    <span className="block truncate">{c.name}</span>
                  </td>
                  <td className="px-3 py-3">
                    <div className="space-y-1.5">
                      <StateBadge state={c.state} />
                      {c.state === "sending" && (
                        <div className="w-28">
                          <ProgressBar
                            label={`${c.name} progress`}
                            value={c.stats.sent}
                            max={c.stats.recipients}
                          />
                        </div>
                      )}
                      {(c.state === "paused" || c.state === "failed") && c.last_error && (
                        <p className="max-w-56 text-xs text-muted-foreground">{c.last_error}</p>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-muted-foreground">{c.segment_name ?? "—"}</td>
                  <td className="px-3 py-3 whitespace-nowrap tabular-nums">
                    {c.state === "draft"
                      ? "—"
                      : `${c.stats.sent.toLocaleString()} of ${c.stats.recipients.toLocaleString()}`}
                  </td>
                  <td className="px-3 py-3 tabular-nums">
                    {c.track_opens && c.state !== "draft"
                      ? percent(c.stats.opened, c.stats.sent)
                      : "—"}
                  </td>
                  <td className="px-3 py-3 tabular-nums">
                    {c.state === "draft" ? "—" : c.stats.unsubscribed.toLocaleString()}
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap text-muted-foreground">
                    {whenText(c)}
                  </td>
                  <td className="px-3 py-3 text-muted-foreground">{c.created_by_name ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {list.hasNextPage && (
        <div className="mt-4 flex justify-center">
          <Button
            variant="outline"
            loading={list.isFetchingNextPage}
            onClick={() => list.fetchNextPage()}
          >
            Load more
          </Button>
        </div>
      )}

      <CampaignSettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}

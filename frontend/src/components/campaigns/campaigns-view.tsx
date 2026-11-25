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
import { InfiniteScroll } from "@/components/ui/infinite-scroll";
import { Button, Card, EmptyState, Select, Skeleton } from "@/components/ui/primitives";
import { DataTable, RowTh, TBody, Td, THead, Th, Tr } from "@/components/ui/table";
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
            variant="ai"
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
        <Card className="rounded-2xl">
          <DataTable minWidth="56rem">
            <THead>
              <tr>
                <Th>Name</Th>
                <Th>State</Th>
                <Th>Segment</Th>
                <Th>Sent</Th>
                <Th>Opened</Th>
                <Th>Unsubscribed</Th>
                <Th>When</Th>
                <Th>Created by</Th>
              </tr>
            </THead>
            <TBody>
              {rows.map((c) => (
                <Tr
                  key={c.id}
                  interactive
                  tabIndex={0}
                  onClick={() => open(c)}
                  onKeyDown={(e) => e.key === "Enter" && open(c)}
                  className="align-top"
                >
                  <RowTh className="max-w-64">
                    <span className="block truncate">{c.name}</span>
                  </RowTh>
                  <Td>
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
                  </Td>
                  <Td muted>{c.segment_name ?? "—"}</Td>
                  <Td className="whitespace-nowrap tabular-nums">
                    {c.state === "draft"
                      ? "—"
                      : `${c.stats.sent.toLocaleString()} of ${c.stats.recipients.toLocaleString()}`}
                  </Td>
                  <Td className="tabular-nums">
                    {c.track_opens && c.state !== "draft"
                      ? percent(c.stats.opened, c.stats.sent)
                      : "—"}
                  </Td>
                  <Td className="tabular-nums">
                    {c.state === "draft" ? "—" : c.stats.unsubscribed.toLocaleString()}
                  </Td>
                  <Td muted className="whitespace-nowrap">
                    {whenText(c)}
                  </Td>
                  <Td muted>{c.created_by_name ?? "—"}</Td>
                </Tr>
              ))}
            </TBody>
          </DataTable>
        </Card>
      )}

      <InfiniteScroll query={list} shown={rows.length} noun="campaigns" className="mt-2" />

      <CampaignSettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}

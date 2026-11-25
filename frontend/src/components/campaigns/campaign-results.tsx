"use client";

import { Download, Pause, Play, Square, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ProgressBar, StateBadge, errorText, percent } from "@/components/campaigns/shared";
import { useConfirm } from "@/components/ui/confirm";
import { InfiniteScroll } from "@/components/ui/infinite-scroll";
import { Badge, Button, Card, EmptyState, Select, Skeleton } from "@/components/ui/primitives";
import { DataTable, RowTh, TBody, Td, THead, Th, Tr } from "@/components/ui/table";
import { LoadError } from "@/components/workspace/list-shared";
import {
  exportCampaignRecipients,
  useCampaignAction,
  useCampaignRecipients,
  useCampaignStats,
} from "@/hooks/use-workspace";
import type { Campaign, CampaignRecipient, CampaignStats } from "@/lib/api/types";
import { formatDateTime } from "@/lib/utils";

const RECIPIENT_FILTERS = [
  "all",
  "queued",
  "sent",
  "failed",
  "bounced",
  "skipped",
  "cancelled",
  "opened",
  "unsubscribed",
  "complained",
] as const;

const STATUS_TONE: Record<string, Parameters<typeof Badge>[0]["tone"]> = {
  sent: "success",
  queued: "info",
  sending: "info",
  failed: "danger",
  bounced: "danger",
  skipped: "neutral",
  cancelled: "neutral",
};

export function CampaignResults({ campaign }: { campaign: Campaign }) {
  const live = campaign.state === "sending";
  const stats = useCampaignStats(campaign.id, live);
  // The list answer is fresh as well; fall back to it until the stats call lands.
  const s = stats.data ?? campaign.stats;

  return (
    <div className="space-y-5">
      <Header campaign={campaign} stats={s} />
      <Numbers campaign={campaign} s={s} />
      <Recipients campaign={campaign} />
    </div>
  );
}

function Header({ campaign, stats }: { campaign: Campaign; stats: CampaignStats }) {
  const pause = useCampaignAction(campaign.id, "pause");
  const resume = useCampaignAction(campaign.id, "resume");
  const cancel = useCampaignAction(campaign.id, "cancel");
  const { ask, dialog } = useConfirm();
  const st = campaign.state;
  const done = stats.sent + stats.failed + stats.skipped;
  const autoPaused = st === "paused" && /bounce/i.test(campaign.last_error ?? "");

  return (
    <Card className="space-y-3 p-5">
      {dialog}
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="min-w-0 flex-1 truncate text-lg font-semibold tracking-tight">
          {campaign.name}
        </h2>
        <StateBadge state={st} />
        {st === "sending" && (
          <Button
            size="sm"
            variant="outline"
            loading={pause.isPending}
            onClick={() => pause.mutate()}
          >
            <Pause className="size-3.5" /> Pause
          </Button>
        )}
        {st === "paused" && (
          <Button
            size="sm"
            variant="outline"
            loading={resume.isPending}
            onClick={() => resume.mutate()}
          >
            <Play className="size-3.5" /> Resume
          </Button>
        )}
        {(st === "sending" || st === "paused") && (
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              ask({
                title: "Stop sending?",
                message: "Customers who did not get it yet won't get it.",
                detail: campaign.name,
                confirmLabel: "Stop sending",
                run: () =>
                  cancel.mutateAsync().then(
                    () => toast.success("Campaign cancelled"),
                    () => {},
                  ),
              })
            }
          >
            <Square className="size-3.5" /> Cancel
          </Button>
        )}
        {st === "scheduled" && (
          <Button
            size="sm"
            variant="outline"
            loading={cancel.isPending}
            onClick={() => cancel.mutate()}
          >
            Cancel schedule
          </Button>
        )}
      </div>

      <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        <Row label="Subject" value={campaign.subject} />
        <Row
          label="From"
          value={
            campaign.from_name
              ? `${campaign.from_name} <${campaign.from_address}>`
              : campaign.from_address
          }
        />
        <Row label="Segment" value={campaign.segment_name} />
        <Row
          label="Times"
          value={[
            campaign.scheduled_at && `scheduled ${formatDateTime(campaign.scheduled_at)}`,
            campaign.started_at && `started ${formatDateTime(campaign.started_at)}`,
            campaign.finished_at && `finished ${formatDateTime(campaign.finished_at)}`,
          ]
            .filter(Boolean)
            .join(" · ")}
        />
      </dl>

      {st === "sending" && (
        <div className="space-y-1">
          <ProgressBar label="Sending progress" value={done} max={stats.recipients} />
          <p className="text-xs text-muted-foreground">
            {done.toLocaleString()} of {stats.recipients.toLocaleString()} done
          </p>
        </div>
      )}

      {autoPaused ? (
        <div
          role="alert"
          className="flex gap-2 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-sm text-warning"
        >
          <TriangleAlert className="mt-0.5 size-4 flex-none" aria-hidden />
          <div>
            <p>{campaign.last_error}</p>
            <p className="mt-1 text-xs">
              CeDesk paused this campaign itself (more than 5% hard bounces after 50 sent). Check
              the segment before you resume.
            </p>
          </div>
        </div>
      ) : (
        (st === "paused" || st === "failed") &&
        campaign.last_error && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            {campaign.last_error}
          </p>
        )
      )}
      {[pause, resume, cancel].map(
        (m, i) =>
          m.isError && (
            <p key={i} role="alert" className="text-sm text-danger">
              {errorText(m.error)}
            </p>
          ),
      )}
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex min-w-0 gap-2">
      <dt className="w-16 flex-none text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate">{value || "—"}</dd>
    </div>
  );
}

function Numbers({ campaign, s }: { campaign: Campaign; s: CampaignStats }) {
  const cards: { label: string; value: string; sub?: string }[] = [
    { label: "Recipients", value: s.recipients.toLocaleString() },
    { label: "Sent", value: s.sent.toLocaleString() },
    { label: "Failed", value: s.failed.toLocaleString() },
    {
      label: "Bounced",
      value: s.bounced.toLocaleString(),
      sub: `${percent(s.bounced, s.sent)} of sent`,
    },
    ...(campaign.track_opens
      ? [
          {
            label: "Opened",
            value: s.opened.toLocaleString(),
            sub: `${percent(s.opened, s.sent)} of sent · ${s.opens.toLocaleString()} opens in all`,
          },
        ]
      : []),
    {
      label: "Unsubscribed",
      value: s.unsubscribed.toLocaleString(),
      sub: `${percent(s.unsubscribed, s.sent)} of sent`,
    },
    { label: "Spam reports", value: s.complained.toLocaleString() },
    { label: "Skipped", value: s.skipped.toLocaleString() },
  ];
  return (
    <div>
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {cards.map((c) => (
          <li key={c.label}>
            <Card className="p-4">
              <p className="text-xs text-muted-foreground">{c.label}</p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">{c.value}</p>
              {c.sub && <p className="mt-0.5 text-xs text-muted-foreground">{c.sub}</p>}
            </Card>
          </li>
        ))}
      </ul>
      {campaign.track_opens && (
        <p className="mt-2 text-xs text-muted-foreground">
          Open counts are a rough guide: some mail apps (for example Apple Mail) load images on
          their own.
        </p>
      )}
    </div>
  );
}

function Recipients({ campaign }: { campaign: Campaign }) {
  const [status, setStatus] = useState<string>("all");
  const [exporting, setExporting] = useState(false);
  const list = useCampaignRecipients(campaign.id, status);
  const rows = list.data?.pages.flatMap((p) => p.recipients) ?? [];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Recipients</h2>
        <Select
          aria-label="Filter recipients"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="ml-2 w-44"
        >
          {RECIPIENT_FILTERS.map((f) => (
            <option key={f} value={f}>
              {f === "all" ? "All recipients" : f[0]!.toUpperCase() + f.slice(1)}
            </option>
          ))}
        </Select>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          loading={exporting}
          onClick={() => {
            setExporting(true);
            exportCampaignRecipients(campaign.id)
              .catch((e) => toast.error(errorText(e)))
              .finally(() => setExporting(false));
          }}
        >
          <Download className="size-3.5" /> Export CSV
        </Button>
      </div>

      {list.isPending ? (
        <Skeleton className="h-40" />
      ) : list.isError ? (
        <LoadError error={list.error} />
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState title="No recipients here">Nothing matches this filter.</EmptyState>
        </Card>
      ) : (
        <Card>
          <DataTable minWidth="52rem">
            <THead>
              <tr>
                <Th>Email</Th>
                <Th>Name</Th>
                <Th>Status</Th>
                <Th>Sent at</Th>
                <Th>Opened</Th>
                <Th>Unsubscribed at</Th>
                <Th>Error</Th>
              </tr>
            </THead>
            <TBody>
              {rows.map((r) => (
                <RecipientRow key={r.id} r={r} />
              ))}
            </TBody>
          </DataTable>
        </Card>
      )}

      <InfiniteScroll query={list} shown={rows.length} noun="recipients" className="mt-2" />
    </div>
  );
}

function RecipientRow({ r }: { r: CampaignRecipient }) {
  const erased = r.email === null;
  return (
    <Tr className="align-top">
      <RowTh className="font-normal">
        {erased ? <span className="text-muted-foreground">—</span> : r.email}
      </RowTh>
      <Td>{erased ? "Erased customer" : (r.name ?? "—")}</Td>
      <Td>
        <Badge tone={STATUS_TONE[r.status] ?? "neutral"}>{r.status}</Badge>
      </Td>
      <Td muted className="whitespace-nowrap">
        {formatDateTime(r.sent_at) || "—"}
      </Td>
      <Td className="tabular-nums">{r.open_count > 0 ? r.open_count : "—"}</Td>
      <Td muted className="whitespace-nowrap">
        {formatDateTime(r.unsubscribed_at) || "—"}
      </Td>
      <Td muted className="max-w-56 text-xs">
        {r.error ?? ""}
      </Td>
    </Tr>
  );
}

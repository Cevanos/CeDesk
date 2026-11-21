"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { errorText } from "@/components/campaigns/shared";
import { Dialog } from "@/components/ui/overlay";
import { Button, Field, Input, Skeleton } from "@/components/ui/primitives";
import { FormError } from "@/components/workspace/list-shared";
import { useCampaignAudience, useSendCampaign } from "@/hooks/use-workspace";
import type { Campaign } from "@/lib/api/types";

const DAY_MS = 86_400_000;

/** `datetime-local` wants "2026-11-21T09:00" in the viewer's own time zone. */
function localInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function SendDialog({
  campaign,
  open,
  onClose,
}: {
  campaign: Campaign;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Dialog open={open} onClose={onClose} title="Send this campaign?" className="max-w-lg">
      {open && <FreshCount campaign={campaign} onClose={onClose} />}
    </Dialog>
  );
}

/** Clicking Send always reads a fresh count first: an older cached answer is never shown. */
function FreshCount({ campaign, onClose }: { campaign: Campaign; onClose: () => void }) {
  const audience = useCampaignAudience(campaign.id);
  const [openedAt] = useState(() => Date.now());
  if (audience.isError) return <FormError message={errorText(audience.error)} />;
  if (!audience.data || audience.dataUpdatedAt < openedAt) return <Skeleton className="h-48" />;
  return <SendForm campaign={campaign} recipients={audience.data.recipients} onClose={onClose} />;
}

function SendForm({
  campaign,
  recipients,
  onClose,
}: {
  campaign: Campaign;
  recipients: number;
  onClose: () => void;
}) {
  const router = useRouter();
  const send = useSendCampaign(campaign.id);
  const [mode, setMode] = useState<"now" | "later">("now");
  const [when, setWhen] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState<string>();
  const [now] = useState(() => Date.now());

  const submit = () => {
    setError(undefined);
    let scheduled_at: string | undefined;
    if (mode === "later") {
      const t = new Date(when).valueOf();
      if (!when || Number.isNaN(t)) return setError("Choose a date and time.");
      if (t <= Date.now()) return setError("Choose a time in the future.");
      if (t > Date.now() + 90 * DAY_MS) return setError("You can schedule up to 90 days ahead.");
      scheduled_at = new Date(t).toISOString();
    }
    send.mutate(
      { scheduled_at },
      {
        onSuccess: (c) => {
          toast.success(c.state === "scheduled" ? "Campaign scheduled" : "Campaign is sending");
          onClose();
          router.push(`/campaigns/${encodeURIComponent(campaign.id)}`);
        },
        onError: (e) => setError(errorText(e)),
      },
    );
  };

  return (
    <div className="space-y-4">
      <p className="text-sm">
        Send <strong>{campaign.name}</strong> to <strong>{recipients.toLocaleString()}</strong>{" "}
        {recipients === 1 ? "customer" : "customers"}?
      </p>
      <dl className="grid grid-cols-[5rem_1fr] gap-x-3 gap-y-1 rounded-lg bg-surface-2 p-3 text-sm">
        <dt className="text-muted-foreground">From</dt>
        <dd className="min-w-0 truncate">
          {campaign.from_name
            ? `${campaign.from_name} <${campaign.from_address}>`
            : campaign.from_address}
        </dd>
        <dt className="text-muted-foreground">Subject</dt>
        <dd className="min-w-0 truncate">{campaign.subject}</dd>
        <dt className="text-muted-foreground">Segment</dt>
        <dd className="min-w-0 truncate">{campaign.segment_name}</dd>
      </dl>

      <fieldset className="space-y-2">
        <legend className="text-[13px] font-medium text-foreground-2">When</legend>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name="when"
            checked={mode === "now"}
            onChange={() => setMode("now")}
          />
          Send now
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name="when"
            checked={mode === "later"}
            onChange={() => setMode("later")}
          />
          Schedule
        </label>
        {mode === "later" && (
          <Field label="Date and time (your time zone)" htmlFor="sd-when">
            <Input
              id="sd-when"
              type="datetime-local"
              value={when}
              min={localInput(new Date(now))}
              max={localInput(new Date(now + 90 * DAY_MS))}
              onChange={(e) => setWhen(e.target.value)}
              className="max-w-64"
            />
          </Field>
        )}
      </fieldset>

      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={agreed}
          onChange={(e) => setAgreed(e.target.checked)}
          className="mt-1"
        />
        I have permission to email these customers.
      </label>

      <FormError message={error} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button disabled={!agreed || recipients === 0} loading={send.isPending} onClick={submit}>
          {mode === "later" ? "Schedule" : "Send"}
        </Button>
      </div>
    </div>
  );
}

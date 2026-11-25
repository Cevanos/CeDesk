"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { errorText } from "@/components/campaigns/shared";
import { DatePicker } from "@/components/ui/date-picker";
import { Dialog } from "@/components/ui/overlay";
import { Button, Field, Skeleton } from "@/components/ui/primitives";
import { FormError } from "@/components/workspace/list-shared";
import { useCampaignAudience, useSendCampaign } from "@/hooks/use-workspace";
import type { Campaign } from "@/lib/api/types";
import { fieldError, useFormik, yup } from "@/lib/forms";

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
  const [error, setError] = useState<string>();
  const [now] = useState(() => Date.now());

  const formik = useFormik({
    initialValues: { mode: "now" as "now" | "later", when: "", agreed: false },
    validationSchema: yup.object({
      mode: yup.string().oneOf(["now", "later"]).required(),
      when: yup.string().when("mode", {
        is: "later",
        then: (f) =>
          f
            .required("Choose a date and time.")
            .test(
              "valid",
              "Choose a date and time.",
              (v) => !!v && !Number.isNaN(new Date(v).valueOf()),
            )
            .test(
              "future",
              "Choose a time in the future.",
              (v) => !v || new Date(v).valueOf() > Date.now(),
            )
            .test(
              "horizon",
              "You can schedule up to 90 days ahead.",
              (v) => !v || new Date(v).valueOf() <= Date.now() + 90 * DAY_MS,
            ),
      }),
      agreed: yup.boolean().oneOf([true], "Confirm that you have permission."),
    }),
    onSubmit: (v) => {
      setError(undefined);
      const scheduled_at = v.mode === "later" ? new Date(v.when).toISOString() : undefined;
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
    },
  });
  const { mode, when, agreed } = formik.values;
  const whenError = fieldError(formik, "when");

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
            onChange={() => void formik.setFieldValue("mode", "now")}
          />
          Send now
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name="when"
            checked={mode === "later"}
            onChange={() => void formik.setFieldValue("mode", "later")}
          />
          Schedule
        </label>
        {mode === "later" && (
          <Field label="Date and time (your time zone)" htmlFor="sd-when" error={whenError}>
            <DatePicker
              id="sd-when"
              type="datetime-local"
              value={when}
              min={localInput(new Date(now))}
              max={localInput(new Date(now + 90 * DAY_MS))}
              onChange={(v) => void formik.setFieldValue("when", v)}
              className="max-w-64"
            />
          </Field>
        )}
      </fieldset>

      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          name="agreed"
          checked={agreed}
          onChange={formik.handleChange}
          className="mt-1"
        />
        I have permission to email these customers.
      </label>

      <FormError message={error} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="ai"
          disabled={!agreed || recipients === 0}
          loading={send.isPending}
          onClick={() => void formik.submitForm()}
        >
          {mode === "later" ? "Schedule" : "Send"}
        </Button>
      </div>
    </div>
  );
}

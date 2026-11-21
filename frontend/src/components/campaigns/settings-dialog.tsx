"use client";

import { useState } from "react";
import { toast } from "sonner";

import { errorText } from "@/components/campaigns/shared";
import { Dialog } from "@/components/ui/overlay";
import { Button, Field, Skeleton, Textarea } from "@/components/ui/primitives";
import { FormError, LoadError, fieldErrors } from "@/components/workspace/list-shared";
import { useCampaignSettings, useSaveCampaignSettings } from "@/hooks/use-workspace";
import type { CampaignSettings } from "@/lib/api/types";

export function CampaignSettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const settings = useCampaignSettings(open);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Campaign settings"
      description="Needed before anything can be sent."
      className="max-w-lg"
    >
      {settings.isPending ? (
        <Skeleton className="h-40" />
      ) : settings.isError ? (
        <LoadError error={settings.error} />
      ) : (
        <SettingsForm settings={settings.data} onClose={onClose} />
      )}
    </Dialog>
  );
}

function SettingsForm({ settings, onClose }: { settings: CampaignSettings; onClose: () => void }) {
  const save = useSaveCampaignSettings();
  const [address, setAddress] = useState(settings.postal_address ?? "");
  const [err, setErr] = useState<string>();

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const value = address.trim();
        if (value.length < 10 || value.length > 300) {
          return setErr("Enter your postal address, 10 to 300 characters.");
        }
        setErr(undefined);
        save.mutate(value, {
          onSuccess: () => {
            toast.success("Postal address saved");
            onClose();
          },
          onError: (e2) => setErr(fieldErrors(e2)._form ?? errorText(e2)),
        });
      }}
    >
      <Field
        label="Postal address"
        htmlFor="cs-address"
        hint="The law (CAN-SPAM and others) needs your company's postal address in every marketing email. It shows in the footer."
      >
        <Textarea
          id="cs-address"
          rows={4}
          maxLength={300}
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          aria-invalid={err ? true : undefined}
        />
      </Field>
      <FormError message={err} />
      <p className="text-xs text-muted-foreground">
        Up to {settings.max_recipients.toLocaleString()} recipients per campaign,{" "}
        {settings.daily_cap.toLocaleString()} campaign emails a day, {settings.per_minute} a minute.
        These limits are set by the server.
      </p>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          Save
        </Button>
      </div>
    </form>
  );
}

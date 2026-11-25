"use client";

import { useState } from "react";
import { toast } from "sonner";

import { errorText } from "@/components/campaigns/shared";
import { Dialog } from "@/components/ui/overlay";
import { Button, Field, Skeleton, Textarea } from "@/components/ui/primitives";
import { FormError, LoadError, fieldErrors } from "@/components/workspace/list-shared";
import { useCampaignSettings, useSaveCampaignSettings } from "@/hooks/use-workspace";
import type { CampaignSettings } from "@/lib/api/types";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";

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
  const [err, setErr] = useState<string>();
  const formik = useFormik({
    initialValues: { address: settings.postal_address ?? "" },
    validationSchema: yup.object({
      address: yup
        .string()
        .trim()
        .required("Enter your postal address, 10 to 300 characters.")
        .min(10, "Enter your postal address, 10 to 300 characters.")
        .max(300, "Enter your postal address, 10 to 300 characters."),
    }),
    onSubmit: (v) => {
      setErr(undefined);
      save.mutate(v.address.trim(), {
        onSuccess: () => {
          toast.success("Postal address saved");
          onClose();
        },
        onError: (e2) => setErr(fieldErrors(e2)._form ?? errorText(e2)),
      });
    },
  });

  return (
    <form noValidate className="space-y-4" onSubmit={formik.handleSubmit}>
      <Field
        label="Postal address"
        htmlFor="cs-address"
        error={fieldError(formik, "address")}
        hint="The law (CAN-SPAM and others) needs your company's postal address in every marketing email. It shows in the footer."
      >
        <Textarea
          id="cs-address"
          rows={4}
          maxLength={300}
          {...bind(formik, "address")}
          aria-invalid={err || fieldError(formik, "address") ? true : undefined}
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

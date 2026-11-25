"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Button, Card, Field, Input, Skeleton, Textarea } from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { FormError, LoadError, fieldErrors } from "@/components/workspace/list-shared";
import { useCurrentUser } from "@/hooks/use-auth";
import { useAutoClose, useSaveAutoClose } from "@/hooks/use-workspace";
import { apiMessage } from "@/lib/api/messages";
import type { AutoCloseSettings } from "@/lib/api/types";
import { bind, fieldError, useFormik } from "@/lib/forms";
import { hasAction } from "@/lib/permissions";
import { formatDateTime } from "@/lib/utils";

export const AFTER_DAYS_MIN = 1;
export const AFTER_DAYS_MAX = 365;
export const NOTICE_MAX = 5000;

/** Check the form before sending; the server checks again and has the final say. */
export function validateAutoClose(days: string, notice: string): Record<string, string> {
  const errs: Record<string, string> = {};
  const n = Number(days);
  if (!days.trim() || !Number.isInteger(n) || n < AFTER_DAYS_MIN || n > AFTER_DAYS_MAX) {
    errs.after_days = `Enter a whole number of days from ${AFTER_DAYS_MIN} to ${AFTER_DAYS_MAX}.`;
  }
  if (notice.length > NOTICE_MAX) {
    errs.notice = `The message can be at most ${NOTICE_MAX} characters.`;
  }
  return errs;
}

export function AutoCloseView() {
  const { user, perms } = useCurrentUser();
  const q = useAutoClose();

  if (!hasAction(user, "settings", perms.manageSettings)) {
    return (
      <Card className="p-5 text-sm text-muted-foreground">
        You don&rsquo;t have access to this setting.
      </Card>
    );
  }
  if (q.isPending) return <Skeleton className="h-64" />;
  if (q.isError) return <LoadError error={q.error} />;
  return <AutoCloseForm data={q.data} />;
}

function AutoCloseForm({ data }: { data: AutoCloseSettings }) {
  const save = useSaveAutoClose();
  const [srv, setSrv] = useState<Record<string, string>>({});
  const form = useFormik({
    initialValues: {
      enabled: data.enabled,
      after_days: String(data.after_days),
      notice: data.notice ?? "",
    },
    // The same checks as before (and their tests); Formik runs them on change, blur and submit.
    validate: (v) => validateAutoClose(v.after_days, v.notice),
    onSubmit: (v) => {
      setSrv({});
      save.mutate(
        { enabled: v.enabled, after_days: Number(v.after_days), notice: v.notice.trim() || null },
        {
          onSuccess: () => toast.success("Auto-close saved"),
          onError: (err) => setSrv({ ...fieldErrors(err), _form: apiMessage(err) }),
        },
      );
    },
  });
  const { enabled, after_days: days, notice } = form.values;
  const errs = {
    after_days: fieldError(form, "after_days") ?? srv.after_days,
    notice: fieldError(form, "notice") ?? srv.notice,
    _form: srv._form,
  };

  const dirty =
    enabled !== data.enabled ||
    days !== String(data.after_days) ||
    notice.trim() !== (data.notice ?? "");

  return (
    <Card className="p-5 sm:p-6">
      <form noValidate onSubmit={form.handleSubmit} className="space-y-5">
        <div className="flex items-start gap-3">
          <Switch
            checked={enabled}
            onChange={(v) => form.setFieldValue("enabled", v)}
            labelledBy="auto-close-label"
            describedBy="auto-close-hint"
          />
          <div className="min-w-0">
            <p id="auto-close-label" className="text-[15px] font-semibold">
              Resolve Pending tickets when the customer doesn&rsquo;t reply
            </p>
            <p id="auto-close-hint" className="mt-1 text-sm text-muted-foreground">
              Snoozed tickets, tickets waiting for an approval or a child ticket, and imported
              tickets are never closed. It runs once a day and is off until you turn it on.
            </p>
          </div>
        </div>

        <Field
          label="After how many days"
          htmlFor="ac-days"
          error={errs.after_days}
          hint="Counted from the customer's last reply."
          className="max-w-xs"
        >
          <div className="flex items-center gap-2">
            <Input
              id="ac-days"
              type="number"
              inputMode="numeric"
              min={AFTER_DAYS_MIN}
              max={AFTER_DAYS_MAX}
              {...bind(form, "after_days")}
              aria-invalid={errs.after_days ? true : undefined}
              aria-describedby={errs.after_days ? "ac-days-error" : undefined}
              className="w-28"
            />
            <span className="text-sm text-muted-foreground">days</span>
          </div>
        </Field>

        <Field
          label="Message to send before closing (optional)"
          htmlFor="ac-notice"
          error={errs.notice}
          hint="Sent as a public reply. Placeholders such as {{customer.first_name}} work like in macros."
        >
          <Textarea
            id="ac-notice"
            rows={5}
            {...bind(form, "notice")}
            placeholder="Hi {{customer.first_name}}, we're closing this for now. Just reply to reopen it."
            aria-invalid={errs.notice ? true : undefined}
          />
          <p className="text-right text-xs text-muted-foreground tabular-nums">
            {notice.length} / {NOTICE_MAX}
          </p>
        </Field>

        <FormError
          message={errs._form && !errs.after_days && !errs.notice ? errs._form : undefined}
        />

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" loading={save.isPending} disabled={!dirty}>
            Save
          </Button>
          {data.updated_at && (
            <span className="text-xs text-muted-foreground">
              Last changed{data.updated_by_name ? ` by ${data.updated_by_name}` : ""} on{" "}
              {formatDateTime(data.updated_at)}
            </span>
          )}
        </div>
      </form>
    </Card>
  );
}

"use client";

import { BadgeCheck, CheckCircle2, ClipboardCheck, Eye, Pencil, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Dialog } from "@/components/ui/overlay";
import { Badge, Button, Card, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { useResolveTicket } from "@/hooks/use-tickets";
import {
  useApps,
  useEditResolution,
  useReceiptSettings,
  useVerifyResolution,
} from "@/hooks/use-workspace";
import { receiptRequired } from "@/hooks/workspace/receipts";
import { ApiError } from "@/lib/api/client";
import type { Resolution, ResolutionInput, Verified } from "@/lib/api/differentiator-types";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";
import { formatDateTime } from "@/lib/utils";

const EDIT_DAYS = 7;

/** Does a receipt need to be sent (or may it be skipped) for a ticket in this team? */
export function useReceiptPolicy(teamId?: string | null) {
  const q = useReceiptSettings();
  const mode = q.data?.mode ?? "off";
  return {
    /** Resolve opens the form. */
    form: mode !== "off",
    /** No "Resolve without receipt". */
    required: receiptRequired(q.data, teamId),
  };
}

/** The customer-facing hint shown beside two of the fields. */
function CustomerSees() {
  return (
    <span className="rounded-full bg-info-soft px-2 py-0.5 text-[11px] font-medium text-info">
      Customer sees this
    </span>
  );
}

type Draft = {
  action_taken: string;
  request: string;
  result: string;
  pending_item: string;
  verified: boolean;
  notified: "auto" | "yes" | "no";
};

const blank: Draft = {
  action_taken: "",
  request: "",
  result: "",
  pending_item: "",
  verified: false,
  notified: "auto",
};

function draftOf(r: Resolution): Draft {
  return {
    action_taken: r.action_taken ?? "",
    request: r.request ?? "",
    result: r.result ?? "",
    pending_item: r.pending_item ?? "",
    verified: r.verified !== "unverified",
    notified: r.customer_notified == null ? "auto" : r.customer_notified ? "yes" : "no",
  };
}

function ReceiptFields({
  id,
  draft,
  set,
  errors,
}: {
  id: string;
  draft: Draft;
  set: (p: Partial<Draft>) => void;
  errors: Record<string, string>;
}) {
  return (
    <div className="space-y-4">
      <Field
        label="What was done"
        htmlFor={`${id}-action`}
        error={errors.action_taken}
        hint={`${draft.action_taken.length}/500`}
      >
        <Textarea
          id={`${id}-action`}
          rows={3}
          maxLength={500}
          value={draft.action_taken}
          aria-invalid={!!errors.action_taken}
          onChange={(e) => set({ action_taken: e.target.value })}
        />
      </Field>
      <Field label="What the customer asked" htmlFor={`${id}-request`} error={errors.request}>
        <Input
          id={`${id}-request`}
          maxLength={300}
          value={draft.request}
          onChange={(e) => set({ request: e.target.value })}
        />
      </Field>
      <Field
        label="Result"
        htmlFor={`${id}-result`}
        error={errors.result}
        action={<CustomerSees />}
        hint="Shown to the customer in the portal once the receipt is verified."
      >
        <Input
          id={`${id}-result`}
          maxLength={300}
          value={draft.result}
          onChange={(e) => set({ result: e.target.value })}
        />
      </Field>
      <Field
        label="Still pending"
        htmlFor={`${id}-pending`}
        error={errors.pending_item}
        action={<CustomerSees />}
        hint="Shown to the customer in the portal as their next step."
      >
        <Input
          id={`${id}-pending`}
          maxLength={200}
          value={draft.pending_item}
          onChange={(e) => set({ pending_item: e.target.value })}
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="flex items-center gap-2 self-end pb-2 text-sm">
          <input
            type="checkbox"
            className="size-4 accent-[var(--primary)]"
            checked={draft.verified}
            onChange={(e) => set({ verified: e.target.checked })}
          />
          I checked this myself
        </label>
        <Field label="Customer notified" htmlFor={`${id}-notified`}>
          <Select
            id={`${id}-notified`}
            value={draft.notified}
            onChange={(e) => set({ notified: e.target.value as Draft["notified"] })}
            options={[
              { value: "auto", label: "Auto (yes if we replied in the last 24 hours)" },
              { value: "yes", label: "Yes" },
              { value: "no", label: "No" },
            ]}
          />
        </Field>
      </div>
    </div>
  );
}

/** Only what is filled in. `notified: auto` is left out. */
function toInput(d: Draft, full: boolean): ResolutionInput {
  const out: ResolutionInput = { action_taken: d.action_taken.trim() };
  const opt = (k: "request" | "result" | "pending_item") => {
    const v = d[k].trim();
    if (v || !full) out[k] = v;
  };
  opt("request");
  opt("result");
  opt("pending_item");
  out.verified = d.verified ? "agent" : "unverified";
  if (d.notified !== "auto") out.customer_notified = d.notified === "yes";
  return out;
}

/** The form that opens on Resolve when receipts are on. */
export function ResolveDialog({
  ticketId,
  label,
  etag,
  required,
  onClose,
}: {
  ticketId: string;
  label: string;
  etag: string | null;
  required: boolean;
  onClose: () => void;
}) {
  const resolve = useResolveTicket(ticketId);
  const form = useFormik<Draft>({
    initialValues: blank,
    validationSchema: draftSchema,
    onSubmit: (v) => resolve.mutate({ etag, resolution: toInput(v, true) }, { onSuccess: done }),
  });
  const draft = form.values;
  const errors: Record<string, string> = {};
  const actionErr = fieldError(form, "action_taken");
  if (actionErr) errors.action_taken = actionErr;
  const set = (p: Partial<Draft>) => void form.setValues({ ...form.values, ...p });

  const done = () => {
    toast.success(`${label} resolved`);
    onClose();
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Resolve ${label}`}
      description={
        required
          ? "A receipt is required for this team."
          : "Record what was done, or resolve without a receipt."
      }
      className="sm:max-w-lg"
    >
      <form noValidate onSubmit={form.handleSubmit}>
        <ReceiptFields id="rv" draft={draft} set={set} errors={errors} />
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          {!required && (
            <Button
              type="button"
              variant="outline"
              disabled={resolve.isPending}
              onClick={() => resolve.mutate({ etag }, { onSuccess: done })}
            >
              Resolve without receipt
            </Button>
          )}
          <Button type="submit" loading={resolve.isPending}>
            <CheckCircle2 className="size-4" /> Resolve with receipt
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function VerifiedBadge({ r }: { r: Resolution }) {
  if (r.verified === "unverified") return <Badge tone="neutral">Not checked</Badge>;
  const text: Record<Exclude<Verified, "unverified">, string> = {
    agent: "Checked by agent",
    shopify: "Verified with Shopify",
    stripe: "Verified with Stripe",
  };
  return (
    <Badge tone="success">
      <BadgeCheck className="size-3" aria-hidden /> {text[r.verified]}
      {r.verified_at && r.verified !== "agent" ? ` · ${formatDateTime(r.verified_at)}` : ""}
    </Badge>
  );
}

const draftSchema = yup.object({
  action_taken: yup.string().trim().required("Say what was done."),
});

const errorText = (e: unknown) => (e instanceof ApiError ? e.userMessage : "Something went wrong.");

/** Within 7 days of the receipt being written. */
export function canEditReceipt(r: Resolution, now = Date.now()): boolean {
  const at = Date.parse(r.created_at ?? "");
  return Number.isNaN(at) || now - at <= EDIT_DAYS * 86_400_000;
}

/** The receipt on a resolved ticket. Shows nothing when there is none. */
export function ReceiptPanel({
  ticketId,
  resolution,
  canChange,
}: {
  ticketId: string;
  resolution: Resolution | null | undefined;
  canChange: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const apps = useApps();
  if (!resolution) return null;

  const installed = (slug: string) => (apps.data ?? []).some((a) => a.app === slug && a.installed);
  const canVerify = canChange && (installed("shopify") || installed("stripe"));
  const editable = canChange && canEditReceipt(resolution);

  const rows: [string, string | null | undefined][] = [
    ["What the customer asked", resolution.request],
    ["Result", resolution.result],
    ["Still pending", resolution.pending_item],
    [
      "Customer notified",
      resolution.customer_notified == null ? null : resolution.customer_notified ? "Yes" : "No",
    ],
  ];

  return (
    <Card className="p-4" aria-label="Resolution receipt">
      <div className="flex flex-wrap items-center gap-2">
        <ClipboardCheck className="size-4 text-primary-ink" aria-hidden />
        <h3 className="text-sm font-semibold">Resolution receipt</h3>
        <VerifiedBadge r={resolution} />
        <div className="ml-auto flex gap-2">
          {canVerify && (
            <Button size="sm" variant="outline" onClick={() => setVerifying(true)}>
              <ShieldCheck className="size-3.5" /> Verify
            </Button>
          )}
          {editable && (
            <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
              <Pencil className="size-3.5" /> Edit
            </Button>
          )}
        </div>
      </div>
      <p className="mt-2 text-sm whitespace-pre-wrap">{resolution.action_taken}</p>
      <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        {rows
          .filter(([, v]) => v)
          .map(([k, v]) => (
            <div key={k} className="min-w-0">
              <dt className="text-xs text-muted-foreground">{k}</dt>
              <dd className="break-words">{v}</dd>
            </div>
          ))}
      </dl>
      <p className="mt-3 text-xs text-muted-foreground">
        {resolution.resolved_by_name ? `Resolved by ${resolution.resolved_by_name}` : "Resolved"}
        {resolution.created_at ? ` · ${formatDateTime(resolution.created_at)}` : ""}
        {resolution.updated_at && resolution.updated_at !== resolution.created_at
          ? ` · edited ${formatDateTime(resolution.updated_at)}`
          : ""}
      </p>
      {editing && (
        <EditDialog ticketId={ticketId} resolution={resolution} onClose={() => setEditing(false)} />
      )}
      {verifying && (
        <VerifyDialog
          ticketId={ticketId}
          shopify={installed("shopify")}
          stripe={installed("stripe")}
          onClose={() => setVerifying(false)}
        />
      )}
    </Card>
  );
}

function EditDialog({
  ticketId,
  resolution,
  onClose,
}: {
  ticketId: string;
  resolution: Resolution;
  onClose: () => void;
}) {
  const edit = useEditResolution(ticketId);
  const [formError, setFormError] = useState<string>();
  const form = useFormik<Draft>({
    initialValues: draftOf(resolution),
    validationSchema: draftSchema,
    onSubmit: (v) => submit(v),
  });
  const draft = form.values;
  const errors: Record<string, string> = {};
  const actionErr = fieldError(form, "action_taken");
  if (actionErr) errors.action_taken = actionErr;
  if (formError) errors._form = formError;
  const set = (p: Partial<Draft>) => {
    setFormError(undefined);
    void form.setValues({ ...form.values, ...p });
  };

  const submit = (v: Draft) => {
    // Only the fields that changed.
    const before = toInput(draftOf(resolution), false);
    const after = toInput(v, false);
    const patch: Record<string, unknown> = {};
    for (const k of Object.keys(after) as (keyof ResolutionInput)[]) {
      if (after[k] !== before[k]) patch[k] = after[k];
    }
    if (Object.keys(patch).length === 0) return onClose();
    edit.mutate(patch as Partial<ResolutionInput>, {
      onSuccess: () => {
        toast.success("Receipt updated");
        onClose();
      },
      onError: (e) => setFormError(errorText(e)),
    });
  };

  return (
    <Dialog open onClose={onClose} title="Edit receipt" className="sm:max-w-lg">
      <form noValidate onSubmit={form.handleSubmit}>
        <ReceiptFields id="ed" draft={draft} set={set} errors={errors} />
        {errors._form && (
          <p role="alert" className="mt-4 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            {errors._form}
          </p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={edit.isPending}>
            Save changes
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function VerifyDialog({
  ticketId,
  shopify,
  stripe,
  onClose,
}: {
  ticketId: string;
  shopify: boolean;
  stripe: boolean;
  onClose: () => void;
}) {
  const verify = useVerifyResolution(ticketId);
  const [apiError, setError] = useState("");
  const form = useFormik<{ kind: "order_id" | "payment_id"; value: string }>({
    initialValues: { kind: shopify ? "order_id" : "payment_id", value: "" },
    validationSchema: yup.object({
      value: yup
        .string()
        .trim()
        .when("kind", {
          is: "order_id",
          then: (x) => x.required("Enter an order id."),
          otherwise: (x) => x.required("Enter a payment id."),
        }),
    }),
    onSubmit: (f) =>
      verify.mutate({ [f.kind]: f.value.trim() } as { order_id: string } | { payment_id: string }, {
        onSuccess: (r) => {
          toast.success(r.result ? r.result : "Receipt verified");
          onClose();
        },
        onError: (e) => setError(errorText(e)),
      }),
  });
  const kind = form.values.kind;
  const error = apiError || fieldError(form, "value") || "";

  return (
    <Dialog
      open
      onClose={onClose}
      title="Verify with Shopify or Stripe"
      description="We only read the order or payment. Nothing is changed there."
    >
      <form noValidate className="space-y-4" onSubmit={form.handleSubmit}>
        {shopify && stripe && (
          <Field label="Check against" htmlFor="vf-kind">
            <Select
              id="vf-kind"
              value={kind}
              onChange={(e) => {
                void form.setFieldValue("kind", e.target.value);
                setError("");
              }}
              options={[
                { value: "order_id", label: "Shopify order" },
                { value: "payment_id", label: "Stripe payment" },
              ]}
            />
          </Field>
        )}
        <Field
          label={kind === "order_id" ? "Shopify order id" : "Stripe payment id"}
          htmlFor="vf-value"
          error={error}
        >
          <Input
            id="vf-value"
            {...bind(form, "value")}
            placeholder={kind === "order_id" ? "1001" : "pi_…"}
            aria-invalid={!!error}
            onChange={(e) => {
              form.handleChange(e);
              setError("");
            }}
          />
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={verify.isPending}>
            <Eye className="size-4" /> Verify
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

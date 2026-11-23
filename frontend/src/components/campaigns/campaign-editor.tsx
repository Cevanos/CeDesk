"use client";

import { CheckCircle2, Eye, Send, TestTube2, Trash2, TriangleAlert } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { PreviewPanel } from "@/components/campaigns/preview-panel";
import { SendDialog } from "@/components/campaigns/send-dialog";
import { errorText } from "@/components/campaigns/shared";
import { useConfirm } from "@/components/ui/confirm";
import { Button, Card, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { FormError } from "@/components/workspace/list-shared";
import { useSegments } from "@/hooks/use-contacts";
import {
  useCampaignAudience,
  useDeleteCampaign,
  useEmailAddresses,
  useSendCampaignTest,
  useUpdateCampaign,
  type CampaignPatch,
} from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { Campaign, EmailAddress } from "@/lib/api/types";

const PLACEHOLDERS = [
  "{{customer.first_name}}",
  "{{customer.name}}",
  "{{agent.name}}",
  "{{fields.<key>}}",
];

/** A warning when the address is not ready to send campaigns: forwarding or DNS is not OK. */
function addressWarning(a: EmailAddress): string | null {
  if (a.forwarding && a.forwarding.status !== "verified") return "forwarding is not verified";
  const bad = [a.dns?.spf, a.dns?.dkim, a.dns?.dmarc].some(
    (c) => c && (c.status === "fail" || c.status === "missing"),
  );
  return bad ? "DNS checks are failing" : null;
}

export function CampaignEditor({ campaign }: { campaign: Campaign }) {
  const router = useRouter();
  const update = useUpdateCampaign(campaign.id);
  const [error, setError] = useState<string>();
  const [previewOpen, setPreviewOpen] = useState(false);
  const [sendOpen, setSendOpen] = useState(false);
  const { ask, dialog } = useConfirm();
  const del = useDeleteCampaign();
  const test = useSendCampaignTest(campaign.id);

  // Local copies of the text fields, saved when the field loses focus.
  const [name, setName] = useState(campaign.name);
  const [subject, setSubject] = useState(campaign.subject);
  const [body, setBody] = useState(campaign.body);
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  const segments = useSegments();
  const addresses = useEmailAddresses();
  const audience = useCampaignAudience(campaign.id);

  const save = (patch: CampaignPatch) => {
    setError(undefined);
    update.mutate(patch, {
      onError: (e) => {
        // 409: it is not a draft any more (someone sent it meanwhile). Show the message, reload.
        setError(errorText(e));
        if (e instanceof ApiError && e.status === 409) router.refresh();
      },
    });
  };
  // Only send what changed, so a blur without an edit makes no call.
  const saveText = (key: "name" | "subject" | "body", value: string) => {
    if (value === campaign[key]) return;
    if (key === "name" && !value.trim()) return setName(campaign.name);
    save({ [key]: key === "name" ? value.trim() : value });
  };

  const insert = (token: string) => {
    const el = bodyRef.current;
    const at = el?.selectionStart ?? body.length;
    const end = el?.selectionEnd ?? at;
    const next = body.slice(0, at) + token + body.slice(end);
    setBody(next);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(at + token.length, at + token.length);
    });
  };

  const problems = campaign.problems;
  const count = audience.data;
  const canSend = problems.length === 0 && !!count && !count.problem && count.recipients > 0;
  const neverStarted = !campaign.started_at;

  return (
    <div>
      {dialog}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <Card className="space-y-4 p-5">
          <Field label="Name (only you see it)" htmlFor="ce-name">
            <Input
              id="ce-name"
              value={name}
              maxLength={120}
              onChange={(e) => setName(e.target.value)}
              onBlur={() => saveText("name", name)}
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Segment" htmlFor="ce-segment">
              <Select
                id="ce-segment"
                value={campaign.segment_id ?? ""}
                placeholder="Choose a segment"
                onChange={(e) => save({ segment_id: e.target.value || null })}
              >
                {segments.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="From" htmlFor="ce-from">
              <Select
                id="ce-from"
                value={campaign.from_address_id ?? ""}
                placeholder="Choose an address"
                onChange={(e) => save({ from_address_id: e.target.value || null })}
              >
                {addresses.data?.map((a) => {
                  const w = addressWarning(a);
                  return (
                    <option key={a.id} value={a.id}>
                      {w ? "⚠ " : ""}
                      {a.address}
                      {w ? ` (${w})` : ""}
                    </option>
                  );
                })}
              </Select>
            </Field>
          </div>
          {(() => {
            const chosen = addresses.data?.find((a) => a.id === campaign.from_address_id);
            const w = chosen && addressWarning(chosen);
            return w ? (
              <p className="flex items-center gap-1.5 text-xs text-warning">
                <TriangleAlert className="size-3.5" aria-hidden /> This address: {w}. Sending will
                be refused until it is fixed in Settings → Email.
              </p>
            ) : null;
          })()}

          <Field
            label="Subject"
            htmlFor="ce-subject"
            hint="Placeholders like {{customer.first_name}} are allowed."
          >
            <Input
              id="ce-subject"
              value={subject}
              maxLength={200}
              onChange={(e) => setSubject(e.target.value)}
              onBlur={() => saveText("subject", subject)}
            />
          </Field>

          <Field
            label="Body"
            htmlFor="ce-body"
            hint="Paragraphs, # headings, - and 1. lists, **bold**, *italics* and [text](https://…) links work. Raw HTML shows as plain text."
          >
            <div className="mb-1.5 flex flex-wrap gap-1.5">
              {PLACEHOLDERS.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => insert(p)}
                  className="rounded-md bg-surface-2 px-2 py-0.5 font-mono text-[11px] text-muted-foreground hover:bg-surface-3 hover:text-foreground"
                >
                  {p}
                </button>
              ))}
            </div>
            <Textarea
              id="ce-body"
              ref={bodyRef}
              rows={14}
              maxLength={50000}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              onBlur={() => saveText("body", body)}
            />
          </Field>

          <div className="flex items-start justify-between gap-4 rounded-lg bg-surface-2 p-3">
            <div>
              <p id="ce-opens-label" className="text-sm font-medium">
                Track opens
              </p>
              <p id="ce-opens-help" className="mt-0.5 text-xs text-muted-foreground">
                Adds a tiny image to count opens. Some mail apps load images on their own (for
                example Apple Mail), so the number is only a rough guide. Leave it off unless you
                need it.
              </p>
            </div>
            <Switch
              checked={campaign.track_opens}
              labelledBy="ce-opens-label"
              describedBy="ce-opens-help"
              onChange={(v) => save({ track_opens: v })}
            />
          </div>

          <FormError message={error} />
        </Card>

        <div className="space-y-4">
          <Card className="space-y-2 p-4">
            <h2 className="text-sm font-semibold">Before you can send</h2>
            {problems.length > 0 ? (
              <ul className="space-y-1 text-sm text-warning">
                {problems.map((p) => (
                  <li key={p} className="flex gap-2">
                    <TriangleAlert className="mt-0.5 size-4 flex-none" aria-hidden />
                    {p}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="flex items-center gap-2 text-sm text-success">
                <CheckCircle2 className="size-4" aria-hidden /> Ready to send
              </p>
            )}
          </Card>

          <Card className="space-y-1.5 p-4">
            <h2 className="text-sm font-semibold">Recipients</h2>
            {!campaign.segment_id ? (
              <p className="text-sm text-muted-foreground">Choose a segment to see the count.</p>
            ) : audience.isPending ? (
              <Skeleton className="h-12" />
            ) : audience.isError ? (
              <p className="text-sm text-danger">{errorText(audience.error)}</p>
            ) : (
              <>
                <p className="text-sm">
                  Will go to <strong>{audience.data.recipients.toLocaleString()}</strong> customers.{" "}
                  {audience.data.in_segment.toLocaleString()} in the segment;{" "}
                  {audience.data.not_mailable.toLocaleString()} left out (no real email address or a
                  support address); {audience.data.suppressed.toLocaleString()} on the suppression
                  list.
                </p>
                {audience.data.problem && (
                  <p role="alert" className="text-sm text-danger">
                    {audience.data.problem}
                  </p>
                )}
              </>
            )}
          </Card>

          <div className="flex flex-col gap-2">
            <Button variant="outline" onClick={() => setPreviewOpen(true)}>
              <Eye className="size-4" /> Preview
            </Button>
            <Button
              variant="outline"
              loading={test.isPending}
              onClick={() =>
                test.mutate(undefined, {
                  onSuccess: (d) => toast.success(`Test sent to ${d.sent_to}.`),
                })
              }
            >
              <TestTube2 className="size-4" /> Send test
            </Button>
            <Button disabled={!canSend} onClick={() => setSendOpen(true)}>
              <Send className="size-4" /> Send
            </Button>
            {neverStarted && (
              <Button
                variant="ghost"
                disabled={del.isPending}
                onClick={() =>
                  ask({
                    title: "Delete this draft?",
                    message: "This can't be undone.",
                    detail: campaign.name,
                    confirmLabel: "Delete",
                    run: () =>
                      del.mutateAsync(campaign.id).then(() => {
                        toast.success("Draft deleted");
                        router.push("/campaigns");
                      }),
                  })
                }
              >
                <Trash2 className="size-4" /> Delete
              </Button>
            )}
          </div>
        </div>
      </div>

      <PreviewPanel
        campaignId={campaign.id}
        open={previewOpen}
        onClose={() => setPreviewOpen(false)}
      />
      <SendDialog campaign={campaign} open={sendOpen} onClose={() => setSendOpen(false)} />
    </div>
  );
}

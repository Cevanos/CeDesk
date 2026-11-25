"use client";

import { useEffect, useState } from "react";

import { errorText } from "@/components/campaigns/shared";
import { Dialog } from "@/components/ui/overlay";
import { Button, Input, Skeleton } from "@/components/ui/primitives";
import { useContactSearch, useDebounced } from "@/hooks/use-contacts";
import { useCampaignPreview } from "@/hooks/use-workspace";
import { bind, useFormik, yup } from "@/lib/forms";
import { cn } from "@/lib/utils";

/**
 * What the email looks like. The HTML goes into an iframe with an empty `sandbox`
 * (no scripts, no same-origin), so nothing in a campaign body can run on this page.
 */
export function PreviewPanel({
  campaignId,
  open,
  onClose,
}: {
  campaignId: string;
  open: boolean;
  onClose: () => void;
}) {
  const preview = useCampaignPreview(campaignId);
  const [contactId, setContactId] = useState<string | undefined>();
  const qForm = useFormik({
    initialValues: { q: "" },
    validationSchema: yup.object({ q: yup.string().max(200, "Use at most 200 characters.") }),
    onSubmit: () => {},
  });
  const q = qForm.values.q;
  const setQ = (v: string) => void qForm.setFieldValue("q", v);
  const [tab, setTab] = useState<"html" | "text">("html");
  const search = useContactSearch(useDebounced(q, 300));
  const run = preview.mutate;

  useEffect(() => {
    if (open) run(contactId);
  }, [open, contactId, run]);

  const d = preview.data;
  return (
    <Dialog open={open} onClose={onClose} title="Preview" className="max-w-3xl">
      <div className="space-y-3">
        <div>
          <label htmlFor="pv-q" className="text-[13px] font-medium text-foreground-2">
            Preview as
          </label>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <Input
              id="pv-q"
              {...bind(qForm, "q")}
              placeholder="Search a customer by name or email"
              className="max-w-xs"
            />
            {contactId && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setContactId(undefined);
                  setQ("");
                }}
              >
                Use the first customer
              </Button>
            )}
          </div>
          {q.trim().length >= 2 && search.data && search.data.length > 0 && !contactId && (
            <ul className="mt-1.5 max-h-40 overflow-y-auto rounded-lg border border-border">
              {search.data.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setContactId(c.id);
                      setQ(c.name || c.email);
                    }}
                    className="flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left text-sm hover:bg-surface-2"
                  >
                    <span className="truncate">{c.name}</span>
                    <span className="truncate text-xs text-muted-foreground">{c.email}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {preview.isPending && !d ? (
          <Skeleton className="h-72" />
        ) : preview.isError ? (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            {errorText(preview.error)}
          </p>
        ) : d ? (
          <>
            <div className="rounded-lg bg-surface-2 px-3 py-2 text-sm">
              <span className="text-muted-foreground">Subject: </span>
              <span className="font-medium">{d.subject}</span>
              {d.contact_name && (
                <span className="ml-2 text-xs text-muted-foreground">for {d.contact_name}</span>
              )}
            </div>
            <div role="tablist" aria-label="Preview format" className="flex gap-1">
              {(["html", "text"] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  role="tab"
                  aria-selected={tab === t}
                  onClick={() => setTab(t)}
                  className={cn(
                    "rounded-lg px-3 py-1 text-[13px] font-medium",
                    tab === t
                      ? "bg-primary/10 text-primary-ink"
                      : "text-muted-foreground hover:bg-surface-2",
                  )}
                >
                  {t === "html" ? "Email" : "Plain text"}
                </button>
              ))}
            </div>
            {tab === "html" ? (
              <iframe
                title="Email preview"
                sandbox=""
                srcDoc={d.html}
                className="h-96 w-full rounded-lg border border-border bg-white"
              />
            ) : (
              <pre className="h-96 overflow-auto rounded-lg border border-border bg-surface-2 p-3 text-sm whitespace-pre-wrap">
                {d.text}
              </pre>
            )}
          </>
        ) : null}
      </div>
    </Dialog>
  );
}

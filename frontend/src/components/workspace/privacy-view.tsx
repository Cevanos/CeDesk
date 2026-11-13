"use client";

import { Download, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Card, Field, Input, Skeleton } from "@/components/ui/primitives";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import {
  privacyFileUrl,
  useEraseCustomer,
  useExportCustomer,
  usePrivacyRequests,
} from "@/hooks/use-workspace";

const when = (s?: string) => (s ? new Date(s).toLocaleString() : "");

export function PrivacyView() {
  return (
    <div className="space-y-6">
      <ExportCard />
      <EraseCard />
      <RequestsCard />
    </div>
  );
}

function ExportCard() {
  const exp = useExportCustomer();
  const [email, setEmail] = useState("");
  const [errs, setErrs] = useState<Record<string, string>>({});
  return (
    <Card className="p-5">
      <h2 className="text-[15px] font-semibold">Export a customer&rsquo;s data</h2>
      <p className="mt-1 mb-4 text-sm text-muted-foreground">
        Everything we hold about one customer, as a JSON file. The file is kept for 7 days.
      </p>
      <form
        noValidate
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (!email.trim()) return setErrs({ email: "Enter the customer's email" });
          setErrs({});
          exp.mutate(email.trim(), {
            onSuccess: () => {
              toast.success("Export started");
              setEmail("");
            },
            onError: (err) => setErrs(fieldErrors(err)),
          });
        }}
      >
        <Field label="Customer email" htmlFor="pv-exp" error={errs.email}>
          <Input
            id="pv-exp"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <FormError message={errs._form} />
        <Button type="submit" loading={exp.isPending}>
          Export
        </Button>
      </form>
    </Card>
  );
}

function EraseCard() {
  const erase = useEraseCustomer();
  const [email, setEmail] = useState("");
  const [confirmEmail, setConfirmEmail] = useState("");
  const [errs, setErrs] = useState<Record<string, string>>({});
  const matches =
    email.trim() !== "" && email.trim().toLowerCase() === confirmEmail.trim().toLowerCase();
  return (
    <Card className="border-danger/30 p-5">
      <h2 className="text-[15px] font-semibold text-danger">Erase a customer</h2>
      <div
        role="note"
        className="mt-2 mb-4 flex gap-2 rounded-lg border border-danger/25 bg-danger-soft px-3 py-2 text-sm text-danger"
      >
        <TriangleAlert className="mt-0.5 size-4 flex-none" aria-hidden />
        <p>This permanently erases the customer and their tickets. It can&rsquo;t be undone.</p>
      </div>
      <form
        noValidate
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (!matches) return setErrs({ confirm: "The two emails don't match" });
          setErrs({});
          erase.mutate(email.trim(), {
            onSuccess: () => {
              toast.success("Customer erased");
              setEmail("");
              setConfirmEmail("");
            },
            onError: (err) => setErrs(fieldErrors(err)),
          });
        }}
      >
        <Field label="Customer email" htmlFor="pv-er" error={errs.email}>
          <Input id="pv-er" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Type the email again to confirm" htmlFor="pv-er2" error={errs.confirm}>
          <Input
            id="pv-er2"
            type="email"
            autoComplete="off"
            value={confirmEmail}
            onChange={(e) => setConfirmEmail(e.target.value)}
          />
        </Field>
        <FormError message={errs._form} />
        <Button type="submit" variant="danger" disabled={!matches} loading={erase.isPending}>
          Erase permanently
        </Button>
      </form>
    </Card>
  );
}

function RequestsCard() {
  const q = usePrivacyRequests();
  return (
    <Card className="overflow-hidden">
      <h2 className="px-4 pt-4 pb-2 text-[15px] font-semibold">Recent requests</h2>
      {q.isPending ? (
        <Skeleton className="m-4 h-16" />
      ) : q.isError ? (
        <div className="p-4">
          <LoadError error={q.error} />
        </div>
      ) : q.data.length === 0 ? (
        <p className="px-4 pb-4 text-sm text-muted-foreground">No exports or erasures yet.</p>
      ) : (
        <ul className="divide-y divide-border">
          {q.data.map((r) => {
            const kind = r.kind ?? r.type ?? "request";
            const isExport = /export/i.test(kind);
            return (
              <li key={r.id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
                <Badge tone={isExport ? "info" : "danger"}>{kind}</Badge>
                <span className="min-w-0 flex-1 basis-48 truncate">
                  {r.email ?? r.customer_email ?? ""}
                </span>
                {r.status && <Badge>{r.status}</Badge>}
                <span className="text-xs text-muted-foreground">{when(r.created_at)}</span>
                {isExport && r.status !== "expired" && (
                  <a
                    href={privacyFileUrl(r.id)}
                    download
                    className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border-strong px-3 text-[13px] hover:bg-surface-2"
                  >
                    <Download className="size-3.5" /> Download
                  </a>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

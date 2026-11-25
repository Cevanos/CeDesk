"use client";

import { Download, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Card, Field, Input, Skeleton } from "@/components/ui/primitives";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import {
  privacyFilePath,
  useEraseCustomer,
  useExportCustomer,
  useFindCustomer,
  usePrivacyRequests,
  type FoundCustomer,
  type PrivacyRequest,
} from "@/hooks/use-workspace";
import { ApiError, downloadFile } from "@/lib/api/client";
import { formatBytes } from "@/lib/format";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";

const when = (s?: string) => (s ? new Date(s).toLocaleString() : "");
const message = (e: unknown) => (e instanceof ApiError ? e.userMessage : "Something went wrong.");

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
  const [srv, setSrv] = useState<Record<string, string>>({});
  const form = useFormik({
    initialValues: { email: "" },
    validationSchema: yup.object({
      email: yup
        .string()
        .trim()
        .required("Enter the customer's email")
        .email("Enter a valid email"),
    }),
    onSubmit: (v, h) => {
      setSrv({});
      exp.mutate(v.email.trim(), {
        onSuccess: () => {
          toast.success("Export ready. Download it from Recent requests.");
          h.resetForm();
        },
        onError: (err) => setSrv({ ...fieldErrors(err), _form: message(err) }),
      });
    },
  });
  return (
    <Card className="p-5">
      <h2 className="text-[15px] font-semibold">Export a customer&rsquo;s data</h2>
      <p className="mt-1 mb-4 text-sm text-muted-foreground">
        Everything we hold about one customer, as a file. The file is kept for 7 days.
      </p>
      <form noValidate className="space-y-3" onSubmit={form.handleSubmit}>
        <Field
          label="Customer email"
          htmlFor="pv-exp"
          error={fieldError(form, "email") ?? srv.email}
        >
          <Input id="pv-exp" type="email" {...bind(form, "email")} />
        </Field>
        <FormError message={srv._form} />
        <Button type="submit" loading={exp.isPending}>
          Export
        </Button>
      </form>
    </Card>
  );
}

function EraseCard() {
  const find = useFindCustomer();
  const erase = useEraseCustomer();
  const [found, setFound] = useState<FoundCustomer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const form = useFormik({
    initialValues: { email: "", confirm_email: "" },
    // Step one needs the email to look up; step two needs the typed confirmation to match.
    validate: (v) => {
      const errs: Record<string, string> = {};
      if (!found) {
        if (!v.email.trim()) errs.email = "Enter the customer's email.";
      } else if (!v.confirm_email.trim()) {
        errs.confirm_email = "Type their email again to confirm.";
      } else if (v.confirm_email.trim().toLowerCase() !== found.email.toLowerCase()) {
        errs.confirm_email = "The two emails don't match.";
      }
      return errs;
    },
    onSubmit: (v) => {
      setError(null);
      if (!found) {
        find.mutate(v.email.trim(), {
          onSuccess: (c) => {
            if (c) {
              setFound(c);
              form.setTouched({}, false);
            } else setError("No customer with that email was found.");
          },
          onError: (err) => setError(message(err)),
        });
        return;
      }
      erase.mutate(
        { contact_id: found.id, confirm_email: v.confirm_email.trim() },
        {
          onSuccess: () => {
            toast.success("Customer erased");
            reset();
          },
          // 422 "email doesn't match", 409 "already erased": shown as they come.
          onError: (err) => setError(message(err)),
        },
      );
    },
  });
  const confirmEmail = form.values.confirm_email;

  const matches =
    !!found &&
    confirmEmail.trim() !== "" &&
    confirmEmail.trim().toLowerCase() === found.email.toLowerCase();

  function reset() {
    form.resetForm();
    setFound(null);
  }

  return (
    <Card className="border-danger/30 p-5">
      <h2 className="text-[15px] font-semibold text-danger">Erase a customer</h2>
      <div
        role="note"
        className="mt-2 mb-4 flex gap-2 rounded-lg border border-danger/25 bg-danger-soft px-3 py-2 text-sm text-danger"
      >
        <TriangleAlert className="mt-0.5 size-4 flex-none" aria-hidden />
        <div>
          <p>
            This permanently erases the customer&rsquo;s personal details and anonymizes their
            tickets. It can&rsquo;t be undone.
          </p>
          <p className="mt-1">A chat kept in Cevanos must be erased in Cevanos too.</p>
        </div>
      </div>

      <form noValidate className="space-y-3" onSubmit={form.handleSubmit}>
        <Field
          label="Find the customer by email"
          htmlFor="pv-er"
          error={found ? undefined : fieldError(form, "email")}
        >
          <div className="flex gap-2">
            <Input id="pv-er" type="email" {...bind(form, "email")} disabled={!!found} />
            {found ? (
              <Button variant="outline" onClick={reset}>
                Change
              </Button>
            ) : (
              <Button type="submit" variant="outline" loading={find.isPending}>
                Find
              </Button>
            )}
          </div>
        </Field>

        {found && (
          <>
            <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm">
              <span className="font-medium">{found.name || "Unnamed customer"}</span>
              <span className="text-muted-foreground"> · {found.email}</span>
            </p>
            <Field
              label="Type their email again to confirm"
              htmlFor="pv-er2"
              error={fieldError(form, "confirm_email")}
            >
              <Input id="pv-er2" type="email" autoComplete="off" {...bind(form, "confirm_email")} />
            </Field>
            <Button type="submit" variant="danger" disabled={!matches} loading={erase.isPending}>
              Erase customer
            </Button>
          </>
        )}
        <FormError message={error ?? undefined} />
      </form>
    </Card>
  );
}

function RequestRow({ r }: { r: PrivacyRequest }) {
  const [busy, setBusy] = useState(false);
  const isExport = r.kind === "export";
  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
      <Badge tone={isExport ? "info" : "danger"}>{r.kind}</Badge>
      <span className="min-w-0 flex-1 basis-48">
        <span className="block truncate">{r.requested_by_name ?? "Someone"}</span>
        {r.customer_erased && (
          <span className="text-xs text-muted-foreground">Customer erased</span>
        )}
      </span>
      {typeof r.size === "number" && r.size > 0 && (
        <span className="text-xs text-muted-foreground tabular-nums">{formatBytes(r.size)}</span>
      )}
      <span className="text-xs text-muted-foreground">{when(r.requested_at)}</span>
      {isExport && r.file_ready && (
        <Button
          size="sm"
          variant="outline"
          loading={busy}
          onClick={() => {
            setBusy(true);
            downloadFile(privacyFilePath(r.id), `customer-export-${r.id}.json`)
              .catch((e) =>
                toast.error(
                  e instanceof ApiError && e.status === 409
                    ? "This file was removed (7 days passed or the customer was erased)."
                    : message(e),
                ),
              )
              .finally(() => setBusy(false));
          }}
        >
          <Download className="size-3.5" /> Download
        </Button>
      )}
    </li>
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
          {q.data.map((r) => (
            <RequestRow key={r.id} r={r} />
          ))}
        </ul>
      )}
    </Card>
  );
}

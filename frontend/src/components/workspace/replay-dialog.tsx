"use client";

import { History } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Dialog } from "@/components/ui/overlay";
import { Button, Field, Input } from "@/components/ui/primitives";
import { useStartReplay, type ReplayTarget } from "@/hooks/workspace/replays";
import { ApiError } from "@/lib/api/client";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";

/**
 * "Replay on past tickets": one input (how many of the newest tickets, 1 to 1000). Tries the rule
 * or workflow on them and shows what it would have done; nothing changes and nothing is sent.
 */
export function ReplayDialog({
  target,
  title,
  onClose,
}: {
  target: ReplayTarget;
  title: string;
  onClose: () => void;
}) {
  const router = useRouter();
  const start = useStartReplay();
  const [error, setError] = useState<string>();
  const [busyId, setBusyId] = useState<string>();
  const form = useFormik({
    initialValues: { limit: "500" },
    validationSchema: yup.object({
      limit: yup
        .number()
        .typeError("Enter a whole number from 1 to 1000.")
        .integer("Enter a whole number from 1 to 1000.")
        .min(1, "Enter a whole number from 1 to 1000.")
        .max(1000, "Enter a whole number from 1 to 1000.")
        .required("Enter a whole number from 1 to 1000."),
    }),
    onSubmit: (v) => {
      setError(undefined);
      start.mutate(
        { target, limit: Number(v.limit) },
        {
          onSuccess: (r) => router.push(`/settings/replays/${encodeURIComponent(r.id)}`),
          onError: (e) => {
            if (e instanceof ApiError && e.status === 409) {
              // The 409 may carry the running replay's id; link to it when it does.
              const id = (e.details as unknown as { replay_id?: string }[])[0]?.replay_id;
              setBusyId(id);
            }
            setError(
              e instanceof ApiError ? e.message || e.userMessage : "Couldn't start the replay.",
            );
          },
        },
      );
    },
  });
  const shownError = fieldError(form, "limit") ?? error;

  return (
    <Dialog
      open
      onClose={onClose}
      title="Replay on past tickets"
      description={`Try “${title}” on your newest tickets. Nothing on any ticket changes and nothing is sent.`}
    >
      <form noValidate className="space-y-4" onSubmit={form.handleSubmit}>
        <Field label="How many of the newest tickets" htmlFor="rp-limit" hint="From 1 to 1000.">
          <Input
            id="rp-limit"
            type="number"
            inputMode="numeric"
            min={1}
            max={1000}
            {...bind(form, "limit")}
            aria-invalid={!!shownError}
          />
        </Field>
        {shownError && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            {shownError}
            {busyId && (
              <>
                {" "}
                <Link
                  href={`/settings/replays/${encodeURIComponent(busyId)}`}
                  className="font-medium underline"
                >
                  Open that replay
                </Link>
              </>
            )}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={start.isPending}>
            <History className="size-4" /> Start replay
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

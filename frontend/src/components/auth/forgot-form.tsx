"use client";

import { ArrowLeft, MailCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button, Field, Input } from "@/components/ui/primitives";
import { useForgotPassword } from "@/hooks/use-auth";
import { ApiError } from "@/lib/api/client";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";

const schema = yup.object({
  workspace: yup.string().trim().required("Enter your workspace").max(40),
  email: yup.string().trim().required("Enter your email").email("Enter a valid email"),
});
type Values = yup.InferType<typeof schema>;

export function ForgotForm() {
  const forgot = useForgotPassword();
  const [state, setState] = useState<"form" | "sent">("form");
  const [error, setError] = useState<string | null>(null);

  const form = useFormik<Values>({
    initialValues: { workspace: "", email: "" },
    validationSchema: schema,
    validateOnBlur: false,
    onSubmit: (raw) => {
      const v = schema.cast(raw) as Values;
      setError(null);
      forgot.mutate(v, {
        // Same answer whether or not the account exists: no user enumeration.
        onSuccess: () => setState("sent"),
        onError: (e) => {
          if (e instanceof ApiError) {
            if (e.status === 422 || e.status === 429 || e.status >= 500 || e.status === 0) {
              return setError(e.userMessage);
            }
            return setState("sent");
          }
          setError("Something went wrong.");
        },
      });
    },
  });

  if (state === "sent") {
    return (
      <div className="animate-rise text-center sm:text-left">
        <div className="mx-auto grid size-12 place-items-center rounded-2xl bg-success-soft text-success sm:mx-0">
          <MailCheck className="size-6" />
        </div>
        <h1 className="mt-5 text-[1.75rem] font-semibold tracking-tight">Check your email</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          If that account exists, we&apos;ve emailed a link to{" "}
          <strong className="text-foreground">{form.values.email}</strong> (workspace{" "}
          <strong className="text-foreground">{form.values.workspace}</strong>). It works for 30
          minutes.
        </p>
        <BackLink />
      </div>
    );
  }

  return (
    <div className="animate-rise">
      <h1 className="text-[1.75rem] font-semibold tracking-tight">Forgot your password?</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">
        Enter your workspace and email and we will send you a reset link.
      </p>

      {error && (
        <div
          role="alert"
          className="mt-6 rounded-lg border border-danger/25 bg-danger-soft px-3.5 py-3 text-sm text-danger"
        >
          {error}
        </div>
      )}

      <form onSubmit={form.handleSubmit} noValidate className="mt-7 space-y-4">
        <Field label="Workspace" htmlFor="workspace" error={fieldError(form, "workspace")}>
          <Input
            id="workspace"
            autoCapitalize="none"
            spellCheck={false}
            {...bind(form, "workspace")}
          />
        </Field>
        <Field label="Email" htmlFor="email" error={fieldError(form, "email")}>
          <Input
            id="email"
            type="email"
            inputMode="email"
            autoComplete="email"
            {...bind(form, "email")}
          />
        </Field>
        <Button type="submit" size="lg" className="w-full" loading={forgot.isPending}>
          Send reset link
        </Button>
      </form>
      <BackLink />
    </div>
  );
}

function BackLink() {
  return (
    <Link
      href="/login"
      className="mt-7 inline-flex items-center gap-1.5 text-sm font-medium text-primary-ink hover:underline"
    >
      <ArrowLeft className="size-4" /> Back to sign in
    </Link>
  );
}

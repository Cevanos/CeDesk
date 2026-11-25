"use client";

import { ArrowLeft, KeyRound, Mail, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { publicErrorMessage } from "@/components/public/errors";
import { Button, Card, Field, Input } from "@/components/ui/primitives";
import { useCodeSignIn, useRequestCode, useWorkspaceSlug } from "@/hooks/use-portal";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";

type Saved = { step: "email" | "code"; email: string };
const savedKey = (ws: string) => `cedesk:portal-signin:${ws}`;

function readSaved(ws: string): Saved {
  try {
    const v = JSON.parse(sessionStorage.getItem(savedKey(ws)) ?? "null") as Saved | null;
    if (v && typeof v.email === "string" && (v.step === "email" || v.step === "code")) {
      return v.step === "code" && !v.email ? { step: "email", email: "" } : v;
    }
  } catch {
    /* storage unavailable or corrupt: start fresh */
  }
  return { step: "email", email: "" };
}

function writeSaved(ws: string, v: Saved) {
  try {
    sessionStorage.setItem(savedKey(ws), JSON.stringify(v));
  } catch {
    /* private mode etc.: state just won't survive a reload */
  }
}

/**
 * Two steps, no password: email -> 6-digit code sent to it. Used on the portal page and
 * inline on a ticket page that was opened from an email while signed out.
 */
export function PortalSignIn({
  title = "Sign in to your tickets",
  intro = "Enter your email and we'll send you a 6-digit code. No password needed.",
  notice,
}: {
  title?: string;
  intro?: string;
  /** Why the customer was signed out (the server's 401 message). */
  notice?: string;
}) {
  const ws = useWorkspaceSlug();
  const request = useRequestCode(ws);
  const signIn = useCodeSignIn(ws);
  // Step + email live in sessionStorage so a remount or full reload returns to "Enter your code".
  const [saved] = useState(() => readSaved(ws));
  const [step, setStepState] = useState<"email" | "code">(saved.step);
  const [error, setError] = useState<string | null>(null);
  const [resent, setResent] = useState(false);
  const codeRef = useRef<HTMLInputElement>(null);

  const emailForm = useFormik({
    initialValues: { email: saved.email },
    validationSchema: yup.object({
      email: yup
        .string()
        .trim()
        .required("Enter a valid email address.")
        .email("Enter a valid email address."),
    }),
    onSubmit: () => sendCode(),
  });
  const codeForm = useFormik({
    initialValues: { code: "" },
    validationSchema: yup.object({
      code: yup
        .string()
        .required("Enter the 6 digits from the email.")
        .matches(/^\d{6}$/, "Enter the 6 digits from the email."),
    }),
    onSubmit: (v) => {
      setError(null);
      signIn.mutate(
        { email: email.trim(), code: v.code },
        {
          onSuccess: () => writeSaved(ws, { step: "email", email: "" }),
          onError: (err) => setError(publicErrorMessage(err)),
        },
      );
    },
  });
  const email = emailForm.values.email;
  const setStep = (s: "email" | "code") => {
    setStepState(s);
    writeSaved(ws, { step: s, email });
  };
  // Move focus to the code box when it appears, so keyboard and screen-reader users land on it.
  useEffect(() => {
    if (step === "code") codeRef.current?.focus();
  }, [step]);

  const sendCode = (after?: () => void) => {
    setError(null);
    request.mutate(email.trim(), {
      onSuccess: () => {
        setStep("code");
        after?.();
      },
      onError: (e) => setError(publicErrorMessage(e)),
    });
  };

  return (
    <Card className="mx-auto w-full max-w-md animate-rise rounded-2xl p-6 shadow-pop sm:p-8">
      <div className="bg-ai-gradient grid size-12 place-items-center rounded-2xl text-white shadow-[0_8px_20px_-8px_var(--ai-from)]">
        {step === "email" ? <Mail className="size-6" /> : <KeyRound className="size-6" />}
      </div>
      <h1 className="mt-5 text-2xl font-semibold tracking-tight">
        {step === "email" ? title : "Enter your code"}
      </h1>
      {notice && step === "email" && (
        <p role="status" className="mt-3 rounded-lg bg-warning-soft px-3 py-2 text-sm">
          {notice}
        </p>
      )}
      <p className="mt-2 text-sm text-muted-foreground">
        {step === "email" ? (
          intro
        ) : (
          <>
            We sent a 6-digit code to <strong className="text-foreground">{email.trim()}</strong>.
            It works for a few minutes.
          </>
        )}
      </p>

      {step === "email" ? (
        <form noValidate className="mt-6 space-y-4" onSubmit={emailForm.handleSubmit}>
          <Field
            label="Email address"
            htmlFor="ps-email"
            error={fieldError(emailForm, "email") ?? error ?? undefined}
          >
            <Input
              id="ps-email"
              type="email"
              inputMode="email"
              autoComplete="email"
              {...bind(emailForm, "email")}
              aria-invalid={!!(fieldError(emailForm, "email") ?? error)}
              aria-describedby={
                fieldError(emailForm, "email") ?? error ? "ps-email-error" : undefined
              }
              onChange={(e) => {
                emailForm.handleChange(e);
                setError(null);
                writeSaved(ws, { step, email: e.target.value });
              }}
            />
          </Field>
          <Button type="submit" size="lg" variant="ai" className="w-full" loading={request.isPending}>
            Send me a code
          </Button>
        </form>
      ) : (
        <form noValidate className="mt-6 space-y-4" onSubmit={codeForm.handleSubmit}>
          <Field
            label="6-digit code"
            htmlFor="ps-code"
            error={fieldError(codeForm, "code") ?? error ?? undefined}
          >
            <Input
              ref={codeRef}
              id="ps-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="\d{6}"
              maxLength={6}
              placeholder="123456"
              className="h-14 text-center font-mono text-2xl tracking-[0.5em]"
              {...bind(codeForm, "code")}
              aria-invalid={!!(fieldError(codeForm, "code") ?? error)}
              aria-describedby={fieldError(codeForm, "code") ?? error ? "ps-code-error" : undefined}
              onChange={(e) => {
                void codeForm.setFieldValue("code", e.target.value.replace(/\D/g, "").slice(0, 6));
                setError(null);
              }}
            />
          </Field>
          <p aria-live="polite" className="sr-only">
            {resent ? "A new code was sent." : ""}
          </p>
          <Button type="submit" size="lg" variant="ai" className="w-full" loading={signIn.isPending}>
            <ShieldCheck className="size-4" /> Sign in
          </Button>
          <div className="flex items-center justify-between text-sm">
            <button
              type="button"
              className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
              onClick={() => {
                setStep("email");
                codeForm.resetForm();
                setError(null);
              }}
            >
              <ArrowLeft className="size-3.5" /> Use another email
            </button>
            <button
              type="button"
              disabled={request.isPending}
              className="font-medium text-primary-ink hover:underline disabled:opacity-50"
              onClick={() => {
                setResent(false);
                sendCode(() => setResent(true));
              }}
            >
              Send a new code
            </button>
          </div>
        </form>
      )}
    </Card>
  );
}

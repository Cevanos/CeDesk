"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, CheckCircle2, LinkIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { PasswordInput, PasswordStrength } from "@/components/auth/password-input";
import { Button, Field } from "@/components/ui/primitives";
import { useResetPassword } from "@/hooks/use-auth";
import { ApiError } from "@/lib/api/client";
import { newPassword, PASSWORD_HINT } from "@/lib/password";

const schema = z
  .object({
    new_password: newPassword(),
    confirm: z.string(),
  })
  .refine((v) => v.new_password === v.confirm, {
    path: ["confirm"],
    message: "Passwords do not match",
  });
type Values = z.infer<typeof schema>;

export function ResetForm() {
  // The emailed link carries the token after `#` so it never reaches server logs.
  // Read it once, then remove it from the address bar (and keep it out of history).
  const [token, setToken] = useState<string | null>(null);
  const found = useRef<string | null>(null);
  useEffect(() => {
    // StrictMode runs this twice in development: the second run must not see an empty address.
    if (found.current === null) {
      const grab = (v: string) => /(?:^|[#?&])token=([^&]+)/.exec(v)?.[1];
      const raw = grab(window.location.hash) ?? grab(window.location.search) ?? "";
      let t = raw;
      try {
        t = decodeURIComponent(raw);
      } catch {
        /* keep raw */
      }
      found.current = t;
      if (t) window.history.replaceState(null, "", window.location.pathname);
    }

    setToken(found.current);
  }, []);
  const reset = useResetPassword();
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    control,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { new_password: "", confirm: "" },
  });

  const pw = useWatch({ control, name: "new_password" }) ?? "";
  const onSubmit = handleSubmit((v) => {
    setError(null);
    reset.mutate(
      { token: token ?? "", new_password: v.new_password },
      {
        onSuccess: () => setDone(true),
        onError: (e) => {
          if (e instanceof ApiError) {
            if (e.status === 400 || e.status === 401 || e.status === 410) {
              return setError("This link is used or expired. Ask for a new one.");
            }
            return setError(e.fieldMessage("new_password") ?? e.userMessage);
          }
          setError("Something went wrong.");
        },
      },
    );
  });

  if (token === null) return null;

  if (!token) {
    return (
      <div className="animate-rise">
        <div className="grid size-12 place-items-center rounded-2xl bg-warning-soft text-warning">
          <LinkIcon className="size-6" />
        </div>
        <h1 className="mt-5 text-[1.75rem] font-semibold tracking-tight">Link not valid</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          This reset link is missing its token. Open the link from your email again, or request a
          new one.
        </p>
        <Link
          href="/forgot-password"
          className="mt-6 inline-block text-sm font-medium text-primary-ink hover:underline"
        >
          Request a new link
        </Link>
      </div>
    );
  }

  if (done) {
    return (
      <div className="animate-rise">
        <div className="grid size-12 place-items-center rounded-2xl bg-success-soft text-success">
          <CheckCircle2 className="size-6" />
        </div>
        <h1 className="mt-5 text-[1.75rem] font-semibold tracking-tight">Password updated</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          You have been signed out everywhere. Sign in with your new password.
        </p>
        <Link href="/login" className="mt-6 inline-flex">
          <Button size="lg">Continue to sign in</Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="animate-rise">
      <h1 className="text-[1.75rem] font-semibold tracking-tight">Set a new password</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">Choose a new password. {PASSWORD_HINT}</p>

      {error && (
        <div
          role="alert"
          className="mt-6 rounded-lg border border-danger/25 bg-danger-soft px-3.5 py-3 text-sm text-danger"
        >
          {error}
        </div>
      )}

      <form onSubmit={onSubmit} noValidate className="mt-7 space-y-4">
        <Field label="New password" htmlFor="new_password" error={errors.new_password?.message}>
          <PasswordInput
            id="new_password"
            autoComplete="new-password"
            aria-invalid={!!errors.new_password}
            {...register("new_password")}
          />
          <div className="pt-1.5">
            <PasswordStrength value={pw} />
          </div>
        </Field>
        <Field label="Confirm password" htmlFor="confirm" error={errors.confirm?.message}>
          <PasswordInput
            id="confirm"
            autoComplete="new-password"
            aria-invalid={!!errors.confirm}
            {...register("confirm")}
          />
        </Field>
        <Button type="submit" size="lg" className="w-full" loading={reset.isPending}>
          Update password
        </Button>
      </form>
      <Link
        href="/login"
        className="mt-7 inline-flex items-center gap-1.5 text-sm font-medium text-primary-ink hover:underline"
      >
        <ArrowLeft className="size-4" /> Back to sign in
      </Link>
    </div>
  );
}

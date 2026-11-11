"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { PasswordInput } from "@/components/auth/password-input";
import { Button, Field, Input } from "@/components/ui/primitives";
import { useSignIn } from "@/hooks/use-auth";
import { ApiError } from "@/lib/api/client";
import { safeNext } from "@/lib/utils";

const schema = z.object({
  workspace: z.string().trim().min(1, "Enter your workspace").max(64),
  email: z.string().trim().min(1, "Enter your email").email("Enter a valid email"),
  password: z.string().min(1, "Enter your password"),
});
type Values = z.infer<typeof schema>;

const WS_KEY = "cdesk.workspace";

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const signIn = useSignIn();

  const {
    register,
    handleSubmit,
    setValue,
    setFocus,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      workspace: process.env.NEXT_PUBLIC_DEFAULT_WORKSPACE ?? "",
      email: "",
      password: "",
    },
  });

  // Remember the workspace (not the password) on this device.
  useEffect(() => {
    try {
      const saved = localStorage.getItem(WS_KEY);
      if (saved) {
        setValue("workspace", saved);
        setFocus("email");
      }
    } catch {
      /* storage blocked: fine */
    }
  }, [setValue, setFocus]);

  const onSubmit = handleSubmit((v) => {
    signIn.mutate(v, {
      onSuccess: () => {
        try {
          localStorage.setItem(WS_KEY, v.workspace);
        } catch {}
        router.replace(safeNext(params.get("next")));
        router.refresh();
      },
    });
  });

  const err = signIn.error instanceof ApiError ? signIn.error : null;
  // Guide §5.1: wrong password, unknown email, inactive and locked all get
  // the same 401 on purpose, with one fixed message.
  const banner = !err
    ? null
    : err.status === 401
      ? "Wrong workspace, email or password."
      : err.status === 429
        ? err.userMessage
        : err.userMessage;

  return (
    <div className="animate-rise">
      <h1 className="text-[1.75rem] font-semibold tracking-tight">Welcome back</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">Sign in to your CDesk workspace.</p>

      {banner && (
        <div
          role="alert"
          className="mt-6 flex items-start gap-2.5 rounded-lg border border-danger/25 bg-danger-soft px-3.5 py-3 text-sm text-danger"
        >
          <AlertCircle className="mt-0.5 size-4 flex-none" />
          <div>
            {banner}
            {err?.requestId && err.status >= 500 && (
              <div className="mt-0.5 font-mono text-[11px] opacity-80">Ref {err.requestId}</div>
            )}
          </div>
        </div>
      )}

      <form onSubmit={onSubmit} noValidate className="mt-7 space-y-4">
        <Field label="Workspace" htmlFor="workspace" error={errors.workspace?.message}>
          <Input
            id="workspace"
            autoComplete="organization"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="acme-store"
            aria-invalid={!!errors.workspace}
            {...register("workspace")}
          />
        </Field>
        <Field label="Email" htmlFor="email" error={errors.email?.message}>
          <Input
            id="email"
            type="email"
            inputMode="email"
            autoComplete="username"
            placeholder="you@company.com"
            aria-invalid={!!errors.email}
            {...register("email")}
          />
        </Field>
        <Field
          label="Password"
          htmlFor="password"
          error={errors.password?.message}
          action={
            <Link
              href="/forgot-password"
              className="text-[13px] font-medium text-primary-ink hover:underline"
            >
              Forgot password?
            </Link>
          }
        >
          <PasswordInput
            id="password"
            autoComplete="current-password"
            placeholder="••••••••••"
            aria-invalid={!!errors.password}
            {...register("password")}
          />
        </Field>

        <Button type="submit" size="lg" className="w-full" loading={signIn.isPending}>
          Sign in
        </Button>
      </form>

      <p className="mt-6 text-center text-xs text-muted-foreground">
        After 10 wrong tries an account is locked for 15 minutes. An admin can unlock it.
      </p>
    </div>
  );
}

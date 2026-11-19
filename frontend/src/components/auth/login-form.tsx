"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { PasswordInput } from "@/components/auth/password-input";
import { Button, Field, Input } from "@/components/ui/primitives";
import { useSignIn, useSsoInfo, useTwoFactorSignIn } from "@/hooks/use-auth";
import { useSignupConfig } from "@/hooks/use-signup";
import { ApiError } from "@/lib/api/client";
import type { SignInResponse } from "@/lib/api/types";
import { safeNext } from "@/lib/utils";

const schema = z.object({
  workspace: z.string().trim().min(1, "Enter your workspace").max(64),
  email: z.string().trim().min(1, "Enter your email").email("Enter a valid email"),
  password: z.string().min(1, "Enter your password"),
});
type Values = z.infer<typeof schema>;

const WS_KEY = "cdesk.workspace";

/** Where to go once signed in. An expired password only gets Change password. */
function landing(r: SignInResponse | undefined, next: string | null) {
  return r?.password_expired ? "/account?expired=1" : safeNext(next);
}

/** The SSO start link: the API's `start_url`, or the old same-origin path if it is missing. */
export function ssoHref(startUrl: string | null, workspace: string, next: string | null) {
  const safe = next && safeNext(next, "") ? safeNext(next, "") : "";
  if (!startUrl) return `/api/sso/${encodeURIComponent(workspace)}/start`;
  try {
    const u = new URL(startUrl);
    if (safe) u.searchParams.set("next", safe);
    return u.toString();
  } catch {
    return `/api/sso/${encodeURIComponent(workspace)}/start`;
  }
}

/** Waits for typing to pause before asking the API about the workspace. */
function useDebounced<T>(value: T, ms = 400) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const signIn = useSignIn();
  const twoFactor = useTwoFactorSignIn();
  const signup = useSignupConfig();
  // A workspace just created (or linked to) fills the field and beats the remembered one.
  const fromUrl = params.get("workspace")?.trim().toLowerCase() ?? "";
  // After a correct password the page asks for the code (the proxy holds the token).
  const [step, setStep] = useState<"password" | "code">("password");
  const [recovery, setRecovery] = useState(false);
  const [code, setCode] = useState("");
  const otpRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (step === "code") otpRef.current?.focus();
  }, [step, recovery]);

  const {
    register,
    handleSubmit,
    setValue,
    setFocus,
    control,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      workspace: fromUrl || (process.env.NEXT_PUBLIC_DEFAULT_WORKSPACE ?? ""),
      email: "",
      password: "",
    },
  });

  const workspace = useWatch({ control, name: "workspace" }) ?? "";
  const sso = useSsoInfo(useDebounced(workspace));
  const ssoOn = sso.data?.enabled === true && workspace.trim() !== "";

  // Remember the workspace (not the password) on this device.
  useEffect(() => {
    if (fromUrl) {
      setFocus("email");
      return;
    }
    try {
      const saved = localStorage.getItem(WS_KEY);
      if (saved) {
        setValue("workspace", saved);
        setFocus("email");
      }
    } catch {
      /* storage blocked: fine */
    }
  }, [setValue, setFocus, fromUrl]);

  const onSubmit = handleSubmit((v) => {
    signIn.mutate(v, {
      onSuccess: (r) => {
        try {
          localStorage.setItem(WS_KEY, v.workspace);
        } catch {}
        if (r?.two_factor_required) {
          setStep("code");
          return;
        }
        router.replace(landing(r, params.get("next")));
        router.refresh();
      },
    });
  });

  const onCode = (e: React.FormEvent) => {
    e.preventDefault();
    const value = recovery ? code.trim() : code.replace(/\s/g, "");
    if (!value) return;
    twoFactor.mutate(
      { code: value, recovery },
      {
        onSuccess: (r) => {
          router.replace(landing(r, params.get("next")));
          router.refresh();
        },
      },
    );
  };

  if (step === "code") {
    const e2 = twoFactor.error instanceof ApiError ? twoFactor.error : null;
    return (
      <div className="animate-rise">
        <span className="grid size-11 place-items-center rounded-xl bg-primary-soft text-primary-ink">
          <ShieldCheck className="size-5" />
        </span>
        <h1 className="mt-4 text-[1.75rem] font-semibold tracking-tight">Two-step sign-in</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">
          {recovery
            ? "Enter one of your recovery codes. Each one works once."
            : "Enter the 6 digit code from your authenticator app."}
        </p>
        {e2 && (
          <div
            role="alert"
            className="mt-6 flex items-start gap-2.5 rounded-lg border border-danger/25 bg-danger-soft px-3.5 py-3 text-sm text-danger"
          >
            <AlertCircle className="mt-0.5 size-4 flex-none" />
            <div>
              {e2.status === 401 || e2.status === 422
                ? "That code didn't work. Check it and try again, or start over if it has been a while."
                : e2.userMessage}
            </div>
          </div>
        )}
        <form onSubmit={onCode} noValidate className="mt-7 space-y-4">
          <Field label={recovery ? "Recovery code" : "Code"} htmlFor="otp">
            <Input
              id="otp"
              ref={otpRef}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              autoComplete="one-time-code"
              inputMode={recovery ? "text" : "numeric"}
              autoCapitalize="none"
              spellCheck={false}
              placeholder={recovery ? "xxxx-xxxx" : "123456"}
            />
          </Field>
          <Button type="submit" size="lg" className="w-full" loading={twoFactor.isPending}>
            Verify
          </Button>
        </form>
        <div className="mt-5 flex items-center justify-between text-[13px]">
          <button
            type="button"
            className="font-medium text-primary-ink hover:underline"
            onClick={() => {
              setRecovery((r) => !r);
              setCode("");
              twoFactor.reset();
            }}
          >
            {recovery ? "Use the app code" : "Use a recovery code"}
          </button>
          <button
            type="button"
            className="text-muted-foreground hover:underline"
            onClick={() => {
              setStep("password");
              setCode("");
              setRecovery(false);
              twoFactor.reset();
              signIn.reset();
            }}
          >
            Start over
          </button>
        </div>
        <p className="mt-6 text-center text-xs text-muted-foreground">
          You have 10 minutes to finish signing in.
        </p>
      </div>
    );
  }

  const err = signIn.error instanceof ApiError ? signIn.error : null;
  // Wrong password, unknown email, inactive and locked all get the same 401
  // on purpose, with one fixed message.
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
        {ssoOn && (
          // A full page load on purpose, to the API's own address: its cookie must belong to the
          // host the identity provider posts back to.
          <a
            href={ssoHref(
              sso.data?.startUrl ?? null,
              workspace.trim().toLowerCase(),
              params.get("next"),
            )}
            className="inline-flex h-11 w-full items-center justify-center rounded-lg border border-border bg-surface text-sm font-medium hover:bg-surface-2"
          >
            Sign in with SSO
          </a>
        )}
      </form>

      {signup.data?.enabled === true && (
        <p className="mt-6 text-center text-sm text-muted-foreground">
          New to CDesk?{" "}
          <Link href="/signup" className="font-medium text-primary-ink hover:underline">
            Create a workspace
          </Link>
        </p>
      )}

      <p className="mt-6 text-center text-xs text-muted-foreground">
        After 10 wrong tries an account is locked for 15 minutes. An admin can unlock it.
      </p>
    </div>
  );
}

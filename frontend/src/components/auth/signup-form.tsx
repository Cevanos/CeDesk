"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, ArrowLeft, MailCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { PasswordInput } from "@/components/auth/password-input";
import { Button, Field, Input } from "@/components/ui/primitives";
import {
  useResendSignupCode,
  useSignupConfig,
  useStartSignup,
  useVerifySignup,
  type SignupStarted,
} from "@/hooks/use-signup";
import { ApiError } from "@/lib/api/client";
import { cleanSlugInput, slugProblem, suggestSlug } from "@/lib/workspace-slug";

const schema = z.object({
  workspace_name: z.string().trim().min(1, "Enter a workspace name").max(120),
  workspace: z.string().superRefine((v, ctx) => {
    const p = slugProblem(v);
    if (p) ctx.addIssue({ code: "custom", message: p });
  }),
  name: z.string().trim().min(1, "Enter your name").max(120),
  email: z.string().trim().min(1, "Enter your email").email("Enter a valid email"),
  password: z.string().min(10, "Use at least 10 characters").max(128, "Use at most 128 characters"),
});
type Values = z.infer<typeof schema>;

/** Seconds from `now` until an ISO time, never below 0. */
function secondsUntil(iso: string | null, now: number): number {
  if (!iso) return 0;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? Math.max(0, Math.ceil((t - now) / 1000)) : 0;
}

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const t = setInterval(tick, 1000);
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, [active]);
  return now;
}

interface TurnstileApi {
  render: (
    el: HTMLElement,
    o: {
      sitekey: string;
      callback: (t: string) => void;
      "expired-callback": () => void;
      "error-callback": () => void;
    },
  ) => string;
  reset: (id?: string) => void;
  remove: (id?: string) => void;
}
declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}
const TURNSTILE_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

/** Cloudflare Turnstile. The loader carries the request nonce (strict-dynamic does the rest). */
function Turnstile({
  siteKey,
  nonce,
  onToken,
  resetSignal,
}: {
  siteKey: string;
  nonce?: string;
  onToken: (t: string | null) => void;
  resetSignal: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const widget = useRef<string | null>(null);
  const onTokenRef = useRef(onToken);
  useEffect(() => {
    onTokenRef.current = onToken;
  });

  useEffect(() => {
    let cancelled = false;
    const mount = () => {
      if (cancelled || !box.current || !window.turnstile || widget.current) return;
      widget.current = window.turnstile.render(box.current, {
        sitekey: siteKey,
        callback: (t) => onTokenRef.current(t),
        "expired-callback": () => onTokenRef.current(null),
        "error-callback": () => onTokenRef.current(null),
      });
    };
    if (window.turnstile) mount();
    else {
      let s = document.querySelector<HTMLScriptElement>("script[data-turnstile]");
      if (!s) {
        s = document.createElement("script");
        s.src = TURNSTILE_SRC;
        s.async = true;
        s.dataset.turnstile = "1";
        if (nonce) s.nonce = nonce;
        document.head.appendChild(s);
      }
      s.addEventListener("load", mount);
    }
    return () => {
      cancelled = true;
      if (widget.current) window.turnstile?.remove(widget.current);
      widget.current = null;
    };
  }, [siteKey, nonce]);

  useEffect(() => {
    if (resetSignal > 0 && widget.current) window.turnstile?.reset(widget.current);
  }, [resetSignal]);

  return <div ref={box} data-testid="turnstile" />;
}

function Banner({ children }: { children: React.ReactNode }) {
  return (
    <div
      role="alert"
      className="mt-6 flex items-start gap-2.5 rounded-lg border border-danger/25 bg-danger-soft px-3.5 py-3 text-sm text-danger"
    >
      <AlertCircle className="mt-0.5 size-4 flex-none" />
      <div>{children}</div>
    </div>
  );
}

function BackLink({ children = "Back to sign in" }: { children?: React.ReactNode }) {
  return (
    <Link
      href="/login"
      className="mt-7 inline-flex items-center gap-1.5 text-sm font-medium text-primary-ink hover:underline"
    >
      <ArrowLeft className="size-4" /> {children}
    </Link>
  );
}

export function SignupForm({ nonce }: { nonce?: string }) {
  const router = useRouter();
  const config = useSignupConfig();
  const start = useStartSignup();
  const verify = useVerifySignup();
  const resend = useResendSignupCode();

  // Held in memory only: not in the URL and not in storage. A refresh starts over.
  const [started, setStarted] = useState<SignupStarted | null>(null);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [newSlug, setNewSlug] = useState<string | null>(null);
  const [slugError, setSlugError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [expiredCode, setExpiredCode] = useState(false);
  const [blockedUntil, setBlockedUntil] = useState<number | null>(null);
  const [captcha, setCaptcha] = useState<string | null>(null);
  const [captchaReset, setCaptchaReset] = useState(0);
  const [off, setOff] = useState(false);
  const slugTouched = useRef(false);
  const codeRef = useRef<HTMLInputElement>(null);

  const {
    register,
    handleSubmit,
    setValue,
    setError,
    setFocus,
    getValues,
    resetField,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { workspace_name: "", workspace: "", name: "", email: "", password: "" },
  });

  const now = useNow(Boolean(started) || blockedUntil !== null);
  const resendIn = secondsUntil(started?.resend_after ?? null, now);
  const blockedFor = blockedUntil ? Math.max(0, Math.ceil((blockedUntil - now) / 1000)) : 0;
  const blocked = blockedFor > 0;

  useEffect(() => {
    if (started) codeRef.current?.focus();
  }, [started]);

  /** Answers every call can give. Returns true when it was handled. */
  const common = useCallback((e: unknown): boolean => {
    if (!(e instanceof ApiError)) {
      setBanner("Something went wrong. Try again.");
      return true;
    }
    if (e.status === 429) {
      setBanner(e.userMessage);
      setBlockedUntil(Date.now() + (e.retryAfter ?? 30) * 1000);
      return true;
    }
    if (e.status === 404 || e.status === 0) {
      if (e.status === 404) setOff(true);
      else setBanner(e.userMessage);
      return true;
    }
    return false;
  }, []);

  const toForm = () => {
    setStarted(null);
    setCode("");
    setNewSlug(null);
    setSlugError(null);
    setExpiredCode(false);
    setBanner(null);
    // What they typed stays, except the password.
    resetField("password", { defaultValue: "" });
    setCaptcha(null);
    setCaptchaReset((n) => n + 1);
  };

  const needsCaptcha = Boolean(config.data?.captchaSiteKey);

  const onSubmit = handleSubmit((v) => {
    setBanner(null);
    if (needsCaptcha && !captcha) {
      setBanner("Complete the check below, then try again.");
      return;
    }
    start.mutate(
      { ...v, captcha_token: captcha },
      {
        onSuccess: (r) => {
          setEmail(v.email.trim());
          setStarted(r);
        },
        onError: (e) => {
          // A CAPTCHA token works once, so every failed try needs a new one.
          setCaptcha(null);
          setCaptchaReset((n) => n + 1);
          if (common(e)) return;
          const err = e as ApiError;
          if (err.status === 409 && err.code === "slug_taken") {
            setError("workspace", { message: "That address is taken. Try another." });
            setFocus("workspace");
          } else if (err.status === 403 && err.code === "captcha_failed") {
            setBanner("The check failed. Please try again.");
          } else if (err.status === 422) {
            let shown = false;
            for (const f of ["workspace", "workspace_name", "name", "email", "password"] as const) {
              const m = err.fieldMessage(f);
              if (m) {
                setError(f, { message: m });
                shown = true;
              }
            }
            if (!shown) setBanner(err.message);
          } else setBanner(err.userMessage);
        },
      },
    );
  });

  const onVerify = (e: React.FormEvent) => {
    e.preventDefault();
    if (!started) return;
    // Pasted "123 456" or "123-456" works; the backend strips them too.
    const clean = code.replace(/[\s-]/g, "");
    if (!clean) return;
    const slug = newSlug !== null ? cleanSlugInput(newSlug).trim() : undefined;
    if (slug !== undefined) {
      const p = slugProblem(slug);
      if (p) {
        setSlugError(p);
        return;
      }
    }
    setBanner(null);
    setSlugError(null);
    verify.mutate(
      { signup_id: started.signup_id, code: clean, ...(slug ? { workspace: slug } : {}) },
      {
        onSuccess: (r) => {
          router.replace(`/login?workspace=${encodeURIComponent(r.workspace)}`);
        },
        onError: (err) => {
          if (common(err)) return;
          const a = err as ApiError;
          if (a.status === 409 && a.code === "slug_taken") {
            // Taken in the meantime: ask for another address, then resend the same code.
            setNewSlug((cur) => cur ?? getValues("workspace"));
            setSlugError("That address was taken while you were signing up. Choose another.");
          } else if (a.status === 422 && a.code === "bad_code") {
            setCode("");
            setBanner("That code didn't work. Check it and try again.");
            codeRef.current?.focus();
          } else if (a.status === 410 && a.code === "signup_expired") {
            toForm();
            setBanner("This sign-up has expired. Please start again.");
          } else if (a.status === 410) {
            setExpiredCode(true);
            setCode("");
            setBanner("That code has expired or was tried too many times. Send a new one.");
          } else setBanner(a.userMessage);
        },
      },
    );
  };

  const onResend = () => {
    if (!started) return;
    setBanner(null);
    resend.mutate(
      { signup_id: started.signup_id },
      {
        onSuccess: (r) => {
          setStarted(r);
          setExpiredCode(false);
          setCode("");
          codeRef.current?.focus();
        },
        onError: (err) => {
          if (common(err)) return;
          const a = err as ApiError;
          if (a.status === 410) {
            toForm();
            setBanner("This sign-up has expired. Please start again.");
          } else setBanner(a.userMessage);
        },
      },
    );
  };

  if (config.isLoading) return <div aria-busy="true" className="min-h-40" />;

  if (off || config.isError || !config.data?.enabled) {
    return (
      <div className="animate-rise">
        <h1 className="text-[1.75rem] font-semibold tracking-tight">
          Sign-up isn&apos;t available
        </h1>
        <p className="mt-1.5 text-sm text-muted-foreground">
          This server doesn&apos;t let people create workspaces themselves. Ask an admin for an
          invite, or sign in to an existing workspace.
        </p>
        <BackLink />
      </div>
    );
  }

  if (started) {
    return (
      <div className="animate-rise">
        <span className="grid size-11 place-items-center rounded-xl bg-primary-soft text-primary-ink">
          <MailCheck className="size-5" />
        </span>
        <h1 className="mt-4 text-[1.75rem] font-semibold tracking-tight">Check your email</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">
          We sent a code to <strong className="text-foreground">{email}</strong>. It works for 10
          minutes.
        </p>
        <div aria-live="polite">{banner && <Banner>{banner}</Banner>}</div>
        <form onSubmit={onVerify} noValidate className="mt-7 space-y-4">
          {newSlug !== null && (
            <Field
              label="Workspace address"
              htmlFor="new-workspace"
              error={slugError ?? undefined}
              hint="Your first choice was taken. Pick another."
            >
              <Input
                id="new-workspace"
                value={newSlug}
                onChange={(e) => setNewSlug(cleanSlugInput(e.target.value))}
                autoCapitalize="none"
                spellCheck={false}
                aria-invalid={!!slugError}
              />
            </Field>
          )}
          <Field
            label="6 digit code"
            htmlFor="signup-code"
            hint="It can take a minute. Check spam."
          >
            <Input
              id="signup-code"
              ref={codeRef}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              inputMode="numeric"
              autoComplete="one-time-code"
              autoCapitalize="none"
              spellCheck={false}
              placeholder="123456"
              maxLength={12}
            />
          </Field>
          <Button
            type="submit"
            size="lg"
            className="w-full"
            loading={verify.isPending}
            disabled={blocked || expiredCode}
          >
            {blocked ? `Verify (wait ${blockedFor}s)` : "Verify"}
          </Button>
        </form>
        <div className="mt-5 flex items-center justify-between text-[13px]">
          <button
            type="button"
            className="font-medium text-primary-ink hover:underline disabled:no-underline disabled:opacity-60"
            disabled={resendIn > 0 || blocked || resend.isPending}
            onClick={onResend}
          >
            {resendIn > 0 ? `Send a new code in ${resendIn}s` : "Send a new code"}
          </button>
          <button type="button" className="text-muted-foreground hover:underline" onClick={toForm}>
            Wrong email? Start again
          </button>
        </div>
      </div>
    );
  }

  const wsReg = register("workspace");
  const nameReg = register("workspace_name");

  return (
    <div className="animate-rise">
      <h1 className="text-[1.75rem] font-semibold tracking-tight">Create a workspace</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">
        Set up CeDesk for your team. We&apos;ll email you a code to confirm.
      </p>
      <div aria-live="polite">{banner && <Banner>{banner}</Banner>}</div>

      <form onSubmit={onSubmit} noValidate className="mt-7 space-y-4">
        <Field
          label="Workspace name"
          htmlFor="workspace_name"
          error={errors.workspace_name?.message}
        >
          <Input
            id="workspace_name"
            autoComplete="organization"
            placeholder="Acme Support"
            aria-invalid={!!errors.workspace_name}
            {...nameReg}
            onChange={(e) => {
              void nameReg.onChange(e);
              if (!slugTouched.current)
                setValue("workspace", suggestSlug(e.target.value), { shouldValidate: false });
            }}
          />
        </Field>
        <Field
          label="Workspace address"
          htmlFor="workspace"
          error={errors.workspace?.message}
          hint="Your team signs in with this. Lowercase letters, digits and dashes."
        >
          <Input
            id="workspace"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="acme-support"
            aria-invalid={!!errors.workspace}
            {...wsReg}
            onChange={(e) => {
              slugTouched.current = true;
              e.target.value = cleanSlugInput(e.target.value);
              void wsReg.onChange(e);
            }}
          />
        </Field>
        <Field label="Your name" htmlFor="name" error={errors.name?.message}>
          <Input
            id="name"
            autoComplete="name"
            placeholder="Priya Shah"
            aria-invalid={!!errors.name}
            {...register("name")}
          />
        </Field>
        <Field label="Email" htmlFor="email" error={errors.email?.message}>
          <Input
            id="email"
            type="email"
            inputMode="email"
            autoComplete="email"
            placeholder="you@company.com"
            aria-invalid={!!errors.email}
            {...register("email")}
          />
        </Field>
        <Field
          label="Password"
          htmlFor="password"
          error={errors.password?.message}
          hint="10 to 128 characters."
        >
          <PasswordInput
            id="password"
            autoComplete="new-password"
            aria-invalid={!!errors.password}
            {...register("password")}
          />
        </Field>

        {config.data.captchaSiteKey && (
          <Turnstile
            siteKey={config.data.captchaSiteKey}
            nonce={nonce}
            onToken={setCaptcha}
            resetSignal={captchaReset}
          />
        )}

        <Button
          type="submit"
          size="lg"
          className="w-full"
          loading={start.isPending}
          disabled={blocked}
        >
          {blocked ? `Create workspace (wait ${blockedFor}s)` : "Create workspace"}
        </Button>
      </form>
      <BackLink>Already have a workspace? Sign in</BackLink>
    </div>
  );
}

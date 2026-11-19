"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import {
  AlertCircle,
  Download,
  KeyRound,
  LogOut,
  ShieldCheck,
  ShieldOff,
  Smartphone,
} from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { QrCode } from "@/components/account/qr-code";
import { PasswordInput, PasswordStrength } from "@/components/auth/password-input";
import { useConfirm } from "@/components/ui/confirm";
import { CopyButton, copyText } from "@/components/ui/copy-button";
import { Dialog } from "@/components/ui/overlay";
import { Badge, Button, Card, Field, Input, Skeleton } from "@/components/ui/primitives";
import {
  secretOf,
  useConfirmTwoFactor,
  useDevices,
  useDisableTwoFactor,
  useNewRecoveryCodes,
  useRemoveDevice,
  useStartTwoFactorSetup,
  useTwoFactorStatus,
  type TwoFactorSetup,
} from "@/hooks/use-account";
import { useChangePassword, useCurrentUser, useSignOut } from "@/hooks/use-auth";
import { ApiError } from "@/lib/api/client";
import { ROLE_LABEL } from "@/lib/permissions";
import { formatDateTime } from "@/lib/utils";

export function AccountView() {
  const params = useSearchParams();
  const { user, isLoading } = useCurrentUser();
  const expired = params.get("expired") === "1";
  const mustSetUp2fa = user?.two_factor_setup_required === true;

  return (
    <div className="mx-auto w-full max-w-3xl px-3 py-5 sm:px-6 sm:py-8">
      <h1 className="text-2xl font-semibold tracking-tight">My account</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        {isLoading ? (
          <Skeleton className="inline-block h-4 w-56 align-middle" />
        ) : user ? (
          <>
            {user.name} · {user.email} · {ROLE_LABEL[user.role]}
          </>
        ) : null}
      </p>

      {expired && (
        <Notice>
          Your password has expired. Choose a new one to carry on. Until then you can only change
          your password or sign out.
        </Notice>
      )}
      {mustSetUp2fa && !expired && (
        <Notice>
          Your workspace requires two-step sign-in. Set it up below to carry on using CDesk.
        </Notice>
      )}

      <div className="mt-6 space-y-5">
        <PasswordCard expired={expired} />
        {/* While the password is expired only /me, /me/password and sign-out work. */}
        {!expired && <TwoFactorCard />}
        {!expired && <DevicesCard />}
        {expired && <SignOutRow />}
      </div>
    </div>
  );
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div
      role="alert"
      className="mt-5 flex items-start gap-2.5 rounded-lg border border-warning/30 bg-warning-soft px-3.5 py-3 text-sm text-foreground"
    >
      <AlertCircle className="mt-0.5 size-4 flex-none text-warning" />
      <div>{children}</div>
    </div>
  );
}

function SignOutRow() {
  const signOut = useSignOut();
  return (
    <Button variant="ghost" onClick={() => signOut.mutate()} loading={signOut.isPending}>
      <LogOut className="size-4" /> Sign out
    </Button>
  );
}

function Section({
  icon,
  title,
  description,
  aside,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description?: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card className="p-5 sm:p-6">
      <div className="flex items-start gap-3">
        <span className="grid size-10 flex-none place-items-center rounded-xl bg-primary-soft text-primary-ink">
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold">{title}</h2>
          {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
        </div>
        {aside}
      </div>
      <div className="mt-5">{children}</div>
    </Card>
  );
}

/* ---------------------------------------------------------------- devices */

function DevicesCard() {
  const q = useDevices();
  const remove = useRemoveDevice();
  const { ask, dialog } = useConfirm();
  return (
    <Section
      icon={<Smartphone className="size-5" />}
      title="My devices"
      description="Phones that get push notifications. Signing out on a phone removes it."
    >
      {dialog}
      {q.isPending ? (
        <Skeleton className="h-12" />
      ) : q.isError ? (
        <p role="alert" className="text-sm text-danger">
          {q.error instanceof ApiError ? q.error.userMessage : "Couldn't load your devices."}
        </p>
      ) : (
        <>
          {!q.data.push_on && (
            <p className="mb-3 rounded-lg bg-surface-2 px-3 py-2 text-sm text-muted-foreground">
              Push notifications are off on this server.
            </p>
          )}
          {q.data.devices.length === 0 ? (
            <p className="text-sm text-muted-foreground">No phones are registered.</p>
          ) : (
            <ul className="divide-y divide-border rounded-lg border border-border">
              {q.data.devices.map((d) => (
                <li key={d.id} className="flex items-center gap-3 px-3 py-2.5 text-sm">
                  <span className="min-w-0 flex-1">
                    <span className="font-medium capitalize">{d.platform}</span>
                    {d.token_hint && (
                      <span className="ml-2 font-mono text-xs text-muted-foreground">
                        …{d.token_hint}
                      </span>
                    )}
                    <span className="block text-xs text-muted-foreground">
                      {d.last_seen_at
                        ? `Last used ${formatDateTime(d.last_seen_at)}`
                        : d.created_at
                          ? `Added ${formatDateTime(d.created_at)}`
                          : ""}
                    </span>
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={remove.isPending}
                    onClick={() =>
                      ask({
                        title: "Remove this phone?",
                        message: "It stops getting notifications.",
                        detail: `${d.platform}${d.token_hint ? ` …${d.token_hint}` : ""}`,
                        confirmLabel: "Remove",
                        run: () =>
                          remove.mutate(d.id, {
                            onSuccess: () => toast.success("Device removed"),
                            onError: (e) =>
                              toast.error(
                                e instanceof ApiError ? e.userMessage : "Couldn't remove it",
                              ),
                          }),
                      })
                    }
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Section>
  );
}

/* ---------------------------------------------------------------- password */

const pwSchema = z
  .object({
    current_password: z.string().min(1, "Enter your current password"),
    new_password: z
      .string()
      .min(10, "Use at least 10 characters")
      .max(128, "Use at most 128 characters"),
    confirm: z.string(),
  })
  .refine((v) => v.new_password === v.confirm, {
    path: ["confirm"],
    message: "Passwords do not match",
  });
type PwValues = z.infer<typeof pwSchema>;

function PasswordCard({ expired }: { expired: boolean }) {
  const router = useRouter();
  const change = useChangePassword();
  const {
    register,
    handleSubmit,
    control,
    reset,
    setError,
    formState: { errors },
  } = useForm<PwValues>({ resolver: zodResolver(pwSchema) });
  const newPw = useWatch({ control, name: "new_password" });

  const onSubmit = handleSubmit((v) =>
    change.mutate(
      { current_password: v.current_password, new_password: v.new_password },
      {
        onSuccess: () => {
          toast.success("Password changed. Other devices were signed out.");
          reset();
          if (expired) {
            router.replace("/tickets");
            router.refresh();
          }
        },
        onError: (e) => {
          if (e instanceof ApiError && (e.status === 422 || e.status === 403)) {
            const fields = e.details.filter(
              (d) => d.field === "current_password" || d.field === "new_password",
            );
            for (const d of fields) {
              setError(d.field as "current_password" | "new_password", { message: d.message });
            }
            // 403 is "old password is wrong".
            if (e.status === 403 && fields.length === 0) {
              setError("current_password", { message: e.message });
            }
          }
        },
      },
    ),
  );

  return (
    <Section
      icon={<KeyRound className="size-5" />}
      title="Change password"
      description="You stay signed in here. Other devices are signed out."
    >
      <form onSubmit={onSubmit} noValidate className="max-w-md space-y-4">
        <Field
          label="Current password"
          htmlFor="ac-current"
          error={errors.current_password?.message}
        >
          <PasswordInput
            id="ac-current"
            autoComplete="current-password"
            aria-invalid={!!errors.current_password}
            {...register("current_password")}
          />
        </Field>
        <Field
          label="New password"
          htmlFor="ac-new"
          error={errors.new_password?.message}
          hint="10 to 128 characters."
        >
          <PasswordInput
            id="ac-new"
            autoComplete="new-password"
            aria-invalid={!!errors.new_password}
            {...register("new_password")}
          />
          <div className="pt-1.5">
            <PasswordStrength value={newPw ?? ""} />
          </div>
        </Field>
        <Field label="Confirm new password" htmlFor="ac-confirm" error={errors.confirm?.message}>
          <PasswordInput
            id="ac-confirm"
            autoComplete="new-password"
            aria-invalid={!!errors.confirm}
            {...register("confirm")}
          />
        </Field>
        <Button type="submit" loading={change.isPending}>
          Update password
        </Button>
      </form>
    </Section>
  );
}

/* --------------------------------------------------------------- two-step */

/** One code box with its own error line. Used to confirm, turn off and renew codes. */
function CodeForm({
  label,
  submitLabel,
  pending,
  error,
  onSubmit,
  onCancel,
  danger,
}: {
  label: string;
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (code: string) => void;
  onCancel?: () => void;
  danger?: boolean;
}) {
  const [code, setCode] = useState("");
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => ref.current?.focus(), []);
  const err = error instanceof ApiError ? error : null;
  return (
    <form
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        const v = code.replace(/\s/g, "");
        if (v) onSubmit(v);
      }}
      className="space-y-3"
    >
      <Field
        label={label}
        htmlFor="tf-code"
        error={err ? (err.fieldMessage("code") ?? err.userMessage) : undefined}
      >
        <Input
          id="tf-code"
          ref={ref}
          value={code}
          onChange={(e) => setCode(e.target.value)}
          autoComplete="one-time-code"
          inputMode="numeric"
          placeholder="123456"
          aria-invalid={!!err}
          className="max-w-48"
        />
      </Field>
      <div className="flex gap-2">
        <Button type="submit" variant={danger ? "danger" : "primary"} loading={pending}>
          {submitLabel}
        </Button>
        {onCancel && (
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}

function TwoFactorCard() {
  const status = useTwoFactorStatus();
  const { user } = useCurrentUser();
  const start = useStartTwoFactorSetup();
  const confirm = useConfirmTwoFactor();
  const disable = useDisableTwoFactor();
  const renew = useNewRecoveryCodes();
  const [setup, setSetup] = useState<TwoFactorSetup | null>(null);
  // Recovery codes are shown once; they live only in this state.
  const [codes, setCodes] = useState<string[] | null>(null);
  const [dialog, setDialog] = useState<"off" | "renew" | null>(null);
  const params = useSearchParams();

  const enabled = status.data?.enabled === true;
  const required = status.data?.required === true;

  // Someone sent here to set it up should not have to hunt for the button.
  const autoStart = params.get("setup") === "2fa" || user?.two_factor_setup_required === true;
  const { mutate: startSetup, isIdle } = start;
  useEffect(() => {
    if (autoStart && status.data && !enabled && isIdle && !setup && !codes) {
      startSetup(undefined, { onSuccess: setSetup });
    }
  }, [autoStart, status.data, enabled, isIdle, setup, codes, startSetup]);

  const begin = () => start.mutate(undefined, { onSuccess: setSetup });

  return (
    <Section
      icon={enabled ? <ShieldCheck className="size-5" /> : <ShieldOff className="size-5" />}
      title="Two-step sign-in"
      description="Ask for a code from an authenticator app when you sign in, on top of your password."
      aside={
        status.data ? (
          <Badge tone={enabled ? "success" : "neutral"}>{enabled ? "On" : "Off"}</Badge>
        ) : null
      }
    >
      {status.isLoading ? (
        <Skeleton className="h-16" />
      ) : status.isError ? (
        <p role="alert" className="text-sm text-danger">
          {status.error instanceof ApiError ? status.error.userMessage : "Couldn't load this."}{" "}
          <button className="font-medium underline" onClick={() => status.refetch()}>
            Try again
          </button>
        </p>
      ) : (
        <div className="space-y-5">
          {codes && <RecoveryCodesPanel codes={codes} onDone={() => setCodes(null)} />}

          {!codes && enabled && (
            <div className="space-y-3">
              <p className="text-sm text-foreground-2">
                Two-step sign-in is on.
                {typeof status.data?.recovery_codes_remaining === "number" &&
                  ` You have ${status.data.recovery_codes_remaining} recovery codes left.`}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={() => setDialog("renew")}>
                  New recovery codes
                </Button>
                <Button
                  variant="secondary"
                  disabled={required}
                  onClick={() => setDialog("off")}
                  title={required ? "Your workspace requires two-step sign-in" : undefined}
                >
                  Turn off
                </Button>
              </div>
              {required && (
                <p className="text-xs text-muted-foreground">
                  Your workspace requires two-step sign-in, so it can&apos;t be turned off.
                </p>
              )}
            </div>
          )}

          {!codes && !enabled && !setup && (
            <Button onClick={begin} loading={start.isPending}>
              Set up two-step sign-in
            </Button>
          )}
          {start.isError && (
            <p role="alert" className="text-sm text-danger">
              {start.error instanceof ApiError ? start.error.userMessage : "Couldn't start setup."}
            </p>
          )}

          {!codes && !enabled && setup && (
            <SetupSteps
              setup={setup}
              pending={confirm.isPending}
              error={confirm.error}
              onConfirm={(code) =>
                confirm.mutate(
                  { code },
                  {
                    onSuccess: (r) => {
                      setSetup(null);
                      setCodes(r?.recovery_codes ?? []);
                      toast.success("Two-step sign-in is on.");
                    },
                  },
                )
              }
              onCancel={() => {
                setSetup(null);
                confirm.reset();
              }}
            />
          )}
        </div>
      )}

      <Dialog
        open={dialog === "off"}
        onClose={() => {
          setDialog(null);
          disable.reset();
        }}
        title="Turn off two-step sign-in"
        description="Enter a current code from your app to confirm."
      >
        <CodeForm
          label="Code"
          submitLabel="Turn off"
          danger
          pending={disable.isPending}
          error={disable.error}
          onCancel={() => setDialog(null)}
          onSubmit={(code) =>
            disable.mutate(
              { code },
              {
                onSuccess: () => {
                  setDialog(null);
                  toast.success("Two-step sign-in is off.");
                },
              },
            )
          }
        />
      </Dialog>

      <Dialog
        open={dialog === "renew"}
        onClose={() => {
          setDialog(null);
          renew.reset();
        }}
        title="New recovery codes"
        description="The old codes stop working. Enter a current code from your app."
      >
        <CodeForm
          label="Code"
          submitLabel="Get new codes"
          pending={renew.isPending}
          error={renew.error}
          onCancel={() => setDialog(null)}
          onSubmit={(code) =>
            renew.mutate(
              { code },
              {
                onSuccess: (r) => {
                  setDialog(null);
                  setCodes(r?.recovery_codes ?? []);
                },
              },
            )
          }
        />
      </Dialog>
    </Section>
  );
}

function SetupSteps({
  setup,
  pending,
  error,
  onConfirm,
  onCancel,
}: {
  setup: TwoFactorSetup;
  pending: boolean;
  error: unknown;
  onConfirm: (code: string) => void;
  onCancel: () => void;
}) {
  const secret = secretOf(setup);
  return (
    <div className="grid gap-6 sm:grid-cols-[auto_1fr]">
      <div className="space-y-2">
        <QrCode text={setup.otpauth_uri} label="QR code for your authenticator app" />
      </div>
      <div className="min-w-0 space-y-4">
        <ol className="list-decimal space-y-1 pl-5 text-sm text-foreground-2">
          <li>Open an authenticator app (Google Authenticator, 1Password, Authy...).</li>
          <li>Scan the code, or type the key in by hand.</li>
          <li>Enter the 6 digit code the app shows.</li>
        </ol>
        {secret && (
          <div>
            <p className="text-xs text-muted-foreground">Can&apos;t scan? Enter this key:</p>
            <div className="mt-1 flex items-center gap-1.5">
              <code className="min-w-0 rounded-md bg-surface-2 px-2 py-1 font-mono text-xs break-all">
                {secret}
              </code>
              <CopyButton text={secret} label="Copy setup key" />
            </div>
          </div>
        )}
        {!secret && (
          <div>
            <p className="text-xs text-muted-foreground">
              Open this link on your phone, or copy it into your app:
            </p>
            <div className="mt-1 flex items-center gap-1.5">
              <code className="min-w-0 rounded-md bg-surface-2 px-2 py-1 font-mono text-xs break-all">
                {setup.otpauth_uri}
              </code>
              <CopyButton text={setup.otpauth_uri} label="Copy setup link" />
            </div>
          </div>
        )}
        <CodeForm
          label="Code from the app"
          submitLabel="Turn on"
          pending={pending}
          error={error}
          onSubmit={onConfirm}
          onCancel={onCancel}
        />
      </div>
    </div>
  );
}

function RecoveryCodesPanel({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const text = codes.join("\n");
  const download = () => {
    const url = URL.createObjectURL(new Blob([text + "\n"], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "cdesk-recovery-codes.txt";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <div className="rounded-xl border border-warning/30 bg-warning-soft p-4">
      <h3 className="text-sm font-semibold">Save your recovery codes</h3>
      <p className="mt-1 text-sm text-foreground-2">
        Each code works once, if you lose your phone. This is the only time they are shown. Keep
        them somewhere safe.
      </p>
      {codes.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">No codes were returned.</p>
      ) : (
        <ul className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-sm sm:max-w-sm">
          {codes.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          variant="secondary"
          onClick={async () =>
            (await copyText(text))
              ? toast.success("Copied")
              : toast.error("Couldn't copy. Select the codes and copy them by hand.")
          }
        >
          Copy all
        </Button>
        <Button variant="secondary" onClick={download}>
          <Download className="size-4" /> Download
        </Button>
        <Button onClick={onDone}>I&apos;ve saved them</Button>
      </div>
    </div>
  );
}

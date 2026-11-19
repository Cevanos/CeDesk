"use client";

import { Download, ShieldCheck, ShieldOff } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { QrCode } from "@/components/account/qr-code";
import { Section } from "@/components/account/section";
import { CopyButton, copyText } from "@/components/ui/copy-button";
import { Dialog } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Skeleton } from "@/components/ui/primitives";
import {
  secretOf,
  useConfirmTwoFactor,
  useDisableTwoFactor,
  useNewRecoveryCodes,
  useStartTwoFactorSetup,
  useTwoFactorStatus,
  type TwoFactorSetup,
} from "@/hooks/use-account";
import { useCurrentUser } from "@/hooks/use-auth";
import { ApiError } from "@/lib/api/client";

/** One code box with its own error line. Used to confirm, turn off and renew codes. */
export function CodeForm({
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

export function TwoFactorCard() {
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

export function SetupSteps({
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

export function RecoveryCodesPanel({ codes, onDone }: { codes: string[]; onDone: () => void }) {
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

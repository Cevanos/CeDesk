"use client";

import { AlertCircle, KeyRound, LogOut, Smartphone, UserRound } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";

import { Section } from "@/components/account/section";
import { TwoFactorCard } from "@/components/account/two-factor-card";
import { PasswordInput, PasswordStrength } from "@/components/auth/password-input";
import { PageHeader } from "@/components/ui/page-header";
import { Button, Field, Skeleton } from "@/components/ui/primitives";
import { useChangePassword, useCurrentUser, useSignOut } from "@/hooks/use-auth";
import { ApiError } from "@/lib/api/client";
import { bind, fieldError, useFormik } from "@/lib/forms";
import {
  changePasswordSchema,
  isWrongCurrentPassword,
  passwordError,
  PASSWORD_HINT,
  type ChangePasswordValues,
} from "@/lib/password";
import { ROLE_LABEL } from "@/lib/permissions";

export function AccountView() {
  const params = useSearchParams();
  const { user, isLoading } = useCurrentUser();
  const expired = params.get("expired") === "1";
  const mustSetUp2fa = user?.two_factor_setup_required === true;

  return (
    <div className="mx-auto w-full max-w-3xl px-3 py-5 sm:px-6 sm:py-8">
      <PageHeader
        icon={UserRound}
        title="My account"
        description={
          isLoading ? (
            <Skeleton className="inline-block h-4 w-56 align-middle" />
          ) : user ? (
            <>
              {user.name} · {user.email} · {ROLE_LABEL[user.role]}
            </>
          ) : null
        }
      />

      {expired && (
        <Notice>
          Your password has expired. Choose a new one to carry on. Until then you can only change
          your password or sign out.
        </Notice>
      )}
      {mustSetUp2fa && !expired && (
        <Notice>
          Your workspace requires two-step sign-in. Set it up below to carry on using CeDesk.
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

function DevicesCard() {
  return (
    <Section
      icon={<Smartphone className="size-5" />}
      title="My devices"
      description="Phones that get push notifications. Signing out on a phone removes it."
    >
      <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-muted-foreground">Coming soon</p>
    </Section>
  );
}

/* ---------------------------------------------------------------- password */

function PasswordCard({ expired }: { expired: boolean }) {
  const router = useRouter();
  const change = useChangePassword();
  const form = useFormik<ChangePasswordValues>({
    initialValues: { current_password: "", new_password: "", confirm: "" },
    validationSchema: changePasswordSchema,
    validateOnBlur: false,
    onSubmit: (v) =>
      change.mutate(
        { current_password: v.current_password, new_password: v.new_password },
        {
          onSuccess: () => {
            toast.success("Password changed. Other devices were signed out.");
            form.resetForm();
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
                form.setFieldError(d.field, d.message);
              }
              // These come back with no field: a wrong current password belongs to that box,
              // the workspace's minimum length (and "choose a different one") to the new one.
              if (fields.length === 0) {
                if (isWrongCurrentPassword(e)) {
                  form.setFieldError("current_password", e.message);
                } else {
                  const pw = passwordError(e, "new_password", { anyMessage: true });
                  if (pw) form.setFieldError("new_password", pw);
                }
              }
              // 403 is "old password is wrong".
              if (e.status === 403 && fields.length === 0) {
                form.setFieldError("current_password", e.message);
              }
            }
          },
        },
      ),
  });

  return (
    <Section
      icon={<KeyRound className="size-5" />}
      title="Change password"
      description="You stay signed in here. Other devices are signed out."
    >
      <form onSubmit={form.handleSubmit} noValidate className="max-w-md space-y-4">
        <Field
          label="Current password"
          htmlFor="ac-current"
          error={fieldError(form, "current_password")}
        >
          <PasswordInput
            id="ac-current"
            autoComplete="current-password"
            {...bind(form, "current_password")}
          />
        </Field>
        <Field
          label="New password"
          htmlFor="ac-new"
          error={fieldError(form, "new_password")}
          hint={PASSWORD_HINT}
        >
          <PasswordInput id="ac-new" autoComplete="new-password" {...bind(form, "new_password")} />
          <div className="pt-1.5">
            <PasswordStrength value={form.values.new_password ?? ""} />
          </div>
        </Field>
        <Field
          label="Confirm new password"
          htmlFor="ac-confirm"
          error={fieldError(form, "confirm")}
        >
          <PasswordInput id="ac-confirm" autoComplete="new-password" {...bind(form, "confirm")} />
        </Field>
        <Button type="submit" loading={change.isPending}>
          Update password
        </Button>
      </form>
    </Section>
  );
}

/* --------------------------------------------------------------- two-step */

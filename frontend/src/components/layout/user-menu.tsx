"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Check, ChevronDown, KeyRound, LogOut, UserCog } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { PasswordInput, PasswordStrength } from "@/components/auth/password-input";
import { ThemeToggle } from "@/components/ui/brand";
import { Dialog, MenuItem, Popover } from "@/components/ui/overlay";
import { Button, Field } from "@/components/ui/primitives";
import { useChangePassword, useCurrentUser, useSetMyStatus, useSignOut } from "@/hooks/use-auth";
import { ApiError } from "@/lib/api/client";
import type { AgentStatus } from "@/lib/api/types";
import { newPassword, passwordError, PASSWORD_HINT } from "@/lib/password";
import { ROLE_LABEL } from "@/lib/permissions";
import { cn, initials } from "@/lib/utils";

export const STATUS_META: Record<AgentStatus, { label: string; dot: string }> = {
  available: { label: "Available", dot: "bg-success" },
  on_break: { label: "On break", dot: "bg-warning" },
  transfers_only: { label: "Transfers only", dot: "bg-info" },
  offline: { label: "Offline", dot: "bg-subtle-foreground" },
};

export function UserMenu() {
  const { user, perms } = useCurrentUser();
  const signOut = useSignOut();
  const setStatus = useSetMyStatus();
  const [pwOpen, setPwOpen] = useState(false);
  const router = useRouter();

  const status = user?.status ?? null;

  return (
    <>
      <Popover
        className="w-72 max-sm:fixed max-sm:inset-x-3 max-sm:top-16 max-sm:w-auto"
        trigger={({ props }) => (
          <button
            {...props}
            aria-label="Account menu"
            className="flex items-center gap-2 rounded-full py-1 pr-2 pl-1 transition-colors hover:bg-surface-2"
          >
            <span className="relative grid size-8 place-items-center rounded-full bg-gradient-to-br from-[#a99bff] to-[#5b3cf0] text-xs font-bold text-white">
              {initials(user?.name)}
              {perms.setOwnStatus && status && (
                <span
                  className={cn(
                    "absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full ring-2 ring-surface",
                    STATUS_META[status].dot,
                  )}
                />
              )}
            </span>
            <ChevronDown className="hidden size-3.5 text-muted-foreground sm:block" />
          </button>
        )}
      >
        {(close) => (
          <div>
            <div className="px-2.5 py-2">
              <div className="truncate text-sm font-semibold">{user?.name ?? "…"}</div>
              <div className="truncate text-xs text-muted-foreground">
                {user ? ROLE_LABEL[user.role] : ""} ·{" "}
                {user?.workspace_name ?? user?.workspace ?? ""}
              </div>
            </div>

            {perms.setOwnStatus && (
              <>
                <div className="my-1 h-px bg-border" />
                <div className="px-2.5 pt-1 pb-0.5 text-[11px] font-semibold tracking-wide text-subtle-foreground uppercase">
                  My status
                </div>
                {(Object.keys(STATUS_META) as AgentStatus[]).map((s) => (
                  <MenuItem
                    key={s}
                    disabled={setStatus.isPending}
                    onClick={() =>
                      setStatus.mutate(s, {
                        onSuccess: () => toast.success(`Status: ${STATUS_META[s].label}`),
                      })
                    }
                  >
                    <span className={cn("size-2 rounded-full", STATUS_META[s].dot)} />
                    {STATUS_META[s].label}
                    {status === s && <Check className="ml-auto size-4 text-primary-ink" />}
                  </MenuItem>
                ))}
                {status === "offline" && (
                  <p className="px-2.5 pt-1 pb-1.5 text-xs text-muted-foreground">
                    You&apos;re offline, so no tickets are routed to you. Switch to Available to
                    start receiving them.
                  </p>
                )}
              </>
            )}

            <div className="my-1 h-px bg-border" />
            <div className="flex items-center justify-between px-2.5 py-1.5">
              <span className="text-sm text-foreground-2">Theme</span>
              <ThemeToggle />
            </div>
            <MenuItem
              onClick={() => {
                close();
                router.push("/account");
              }}
            >
              <UserCog className="size-4 text-muted-foreground" /> My account
            </MenuItem>
            <MenuItem
              onClick={() => {
                close();
                setPwOpen(true);
              }}
            >
              <KeyRound className="size-4 text-muted-foreground" /> Change password
            </MenuItem>
            <MenuItem onClick={() => signOut.mutate()} disabled={signOut.isPending}>
              <LogOut className="size-4 text-muted-foreground" /> Sign out
            </MenuItem>
          </div>
        )}
      </Popover>

      <ChangePasswordDialog open={pwOpen} onClose={() => setPwOpen(false)} />
    </>
  );
}

const schema = z
  .object({
    current_password: z.string().min(1, "Enter your current password"),
    new_password: newPassword(),
    confirm: z.string(),
  })
  .refine((v) => v.new_password === v.confirm, {
    path: ["confirm"],
    message: "Passwords do not match",
  });
type Values = z.infer<typeof schema>;

function ChangePasswordDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const change = useChangePassword();
  const {
    register,
    handleSubmit,
    control,
    reset,
    setError,
    formState: { errors },
  } = useForm<Values>({ resolver: zodResolver(schema) });

  const newPw = useWatch({ control, name: "new_password" });
  const close = () => {
    reset();
    change.reset();
    onClose();
  };

  const onSubmit = handleSubmit((v) =>
    change.mutate(
      { current_password: v.current_password, new_password: v.new_password },
      {
        onSuccess: () => {
          toast.success("Password changed. Other devices were signed out.");
          close();
        },
        onError: (e) => {
          if (e instanceof ApiError && e.status === 422) {
            for (const d of e.details) {
              if (d.field === "current_password" || d.field === "new_password") {
                setError(d.field, { message: d.message });
              }
            }
            // The workspace's own minimum length comes back without a field.
            const pw = passwordError(e, "new_password");
            if (pw && e.details.length === 0) setError("new_password", { message: pw });
          }
        },
      },
    ),
  );

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Change password"
      description="You stay signed in here. Other devices are signed out."
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        <Field
          label="Current password"
          htmlFor="cp-current"
          error={errors.current_password?.message}
        >
          <PasswordInput
            id="cp-current"
            autoComplete="current-password"
            aria-invalid={!!errors.current_password}
            {...register("current_password")}
          />
        </Field>
        <Field
          label="New password"
          htmlFor="cp-new"
          error={errors.new_password?.message}
          hint={PASSWORD_HINT}
        >
          <PasswordInput
            id="cp-new"
            autoComplete="new-password"
            aria-invalid={!!errors.new_password}
            {...register("new_password")}
          />
          <div className="pt-1.5">
            <PasswordStrength value={newPw ?? ""} />
          </div>
        </Field>
        <Field label="Confirm new password" htmlFor="cp-confirm" error={errors.confirm?.message}>
          <PasswordInput
            id="cp-confirm"
            autoComplete="new-password"
            aria-invalid={!!errors.confirm}
            {...register("confirm")}
          />
        </Field>
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" loading={change.isPending}>
            Update password
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

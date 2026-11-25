"use client";

import { KeyRound, Lock, ShieldOff, UserPlus } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { PasswordInput } from "@/components/auth/password-input";
import { useConfirm } from "@/components/ui/confirm";
import { InfiniteScroll } from "@/components/ui/infinite-scroll";
import { Dialog } from "@/components/ui/overlay";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  FormSelect,
  Input,
  Select,
  Skeleton,
} from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import {
  useAddMember,
  useMembers,
  useResetMember2fa,
  useResetMemberPassword,
  useUpdateMember,
} from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { Member, Role } from "@/lib/api/types";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";
import { newPassword, passwordError, PASSWORD_HINT } from "@/lib/password";
import { ROLE_LABEL } from "@/lib/permissions";
import { initials } from "@/lib/utils";

const ROLES: Role[] = ["light_agent", "agent", "supervisor", "admin"];

export function MembersView() {
  const { user } = useCurrentUser();
  const q = useMembers();
  const update = useUpdateMember();
  const reset2fa = useResetMember2fa();
  const { ask, dialog } = useConfirm();
  const [adding, setAdding] = useState(false);
  const [resetFor, setResetFor] = useState<Member | null>(null);
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data]);

  return (
    <div>
      {dialog}
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          New members start Offline until they set themselves Available.
        </p>
        <Button onClick={() => setAdding(true)}>
          <UserPlus className="size-4" /> Add member
        </Button>
      </div>

      <Card className="overflow-hidden">
        {q.isPending ? (
          <div className="space-y-3 p-4">
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
          </div>
        ) : q.isError ? (
          <EmptyState title="We couldn't load members">
            {q.error instanceof ApiError ? q.error.userMessage : ""}
          </EmptyState>
        ) : (
          <ul className="divide-y divide-border">
            {rows.map((m) => {
              const self = m.id === user?.id;
              return (
                <li
                  key={m.id}
                  className="flex flex-wrap items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-2/60"
                >
                  <span
                    aria-hidden
                    className="grid size-10 flex-none place-items-center rounded-full bg-ai-gradient text-[13px] font-bold text-white shadow-card"
                  >
                    {initials(m.name)}
                  </span>
                  <div className="min-w-0 flex-1 basis-48">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{m.name}</span>
                      {self && <Badge tone="primary">You</Badge>}
                      {!m.is_active && <Badge>Inactive</Badge>}
                      {m.locked && (
                        <Badge tone="danger">
                          <Lock className="size-3" /> Locked
                        </Badge>
                      )}
                    </div>
                    <div className="truncate text-sm text-muted-foreground">{m.email}</div>
                  </div>
                  <Select
                    aria-label={`Role for ${m.name}`}
                    className="w-full sm:w-40"
                    controlClassName="min-h-9"
                    value={m.role}
                    disabled={update.isPending}
                    onChange={(e) =>
                      update.mutate(
                        { id: m.id, role: e.target.value as Role },
                        { onSuccess: () => toast.success("Role updated") },
                      )
                    }
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {ROLE_LABEL[r]}
                      </option>
                    ))}
                  </Select>
                  <div className="flex gap-1.5">
                    {!self && (
                      <Button size="sm" variant="outline" onClick={() => setResetFor(m)}>
                        <KeyRound className="size-3.5" /> Reset password
                      </Button>
                    )}
                    {!self && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={reset2fa.isPending}
                        onClick={() =>
                          ask({
                            title: "Reset two-step sign-in?",
                            message:
                              "They'll be signed out everywhere and set it up again at next sign-in.",
                            detail: m.name,
                            confirmLabel: "Reset 2FA",
                            tone: "warning",
                            run: () =>
                              reset2fa.mutate(m.id, {
                                onSuccess: () => toast.success(`2FA reset for ${m.name}.`),
                              }),
                          })
                        }
                      >
                        <ShieldOff className="size-3.5" /> Reset 2FA
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={update.isPending}
                      onClick={() => {
                        const toggle = () =>
                          update.mutate(
                            { id: m.id, is_active: !m.is_active },
                            // e.g. "the last active Admin can't be removed" comes back as the message.
                            {
                              onSuccess: () =>
                                toast.success(
                                  m.is_active ? "Member deactivated" : "Member reactivated",
                                ),
                            },
                          );
                        if (!m.is_active) return toggle();
                        ask({
                          title: "Deactivate this member?",
                          message: "They can no longer sign in. You can reactivate them later.",
                          detail: m.name,
                          confirmLabel: "Deactivate",
                          run: toggle,
                        });
                      }}
                    >
                      {m.is_active ? "Deactivate" : "Reactivate"}
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        <InfiniteScroll
          query={q}
          shown={rows.length}
          noun="members"
          className="border-t border-border"
        />
      </Card>

      <AddMemberDialog open={adding} onClose={() => setAdding(false)} />
      <ResetPasswordDialog member={resetFor} onClose={() => setResetFor(null)} />
    </div>
  );
}

const addSchema = yup.object({
  name: yup.string().trim().required("Enter a name").max(120),
  email: yup.string().trim().required("Enter an email").email("Enter a valid email"),
  role: yup.mixed<Role>().oneOf(ROLES).required(),
  password: newPassword(),
});
type AddValues = yup.InferType<typeof addSchema>;

function AddMemberDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const add = useAddMember();
  const form = useFormik<AddValues>({
    initialValues: { name: "", email: "", role: "agent", password: "" },
    validationSchema: addSchema,
    onSubmit: (v, helpers) => {
      const values = addSchema.cast(v) as AddValues;
      add.mutate(values, {
        onSuccess: () => {
          toast.success(`${values.name} added`);
          close();
        },
        onError: (e) => {
          if (e instanceof ApiError && e.status === 422) {
            for (const d of e.details)
              if (d.field in addSchema.fields) helpers.setFieldError(d.field, d.message);
            // The workspace's own minimum length comes back without a field.
            const pw = passwordError(e, "password");
            if (pw) helpers.setFieldError("password", pw);
          }
        },
      });
    },
  });

  const close = () => {
    form.resetForm();
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Add member"
      description="Share the temporary password with them privately."
    >
      <form noValidate className="space-y-4" onSubmit={form.handleSubmit}>
        <Field label="Name" htmlFor="m-name" error={fieldError(form, "name")}>
          <Input id="m-name" autoComplete="off" {...bind(form, "name")} />
        </Field>
        <Field label="Email" htmlFor="m-email" error={fieldError(form, "email")}>
          <Input id="m-email" type="email" autoComplete="off" {...bind(form, "email")} />
        </Field>
        <Field label="Role" htmlFor="m-role">
          <FormSelect form={form} id="m-role" name="role">
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </FormSelect>
        </Field>
        <Field
          label="Temporary password"
          htmlFor="m-pw"
          error={fieldError(form, "password")}
          hint={PASSWORD_HINT}
        >
          <PasswordInput id="m-pw" autoComplete="new-password" {...bind(form, "password")} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" loading={add.isPending}>
            Add member
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

const resetSchema = yup.object({
  new_password: newPassword(),
});

function ResetPasswordDialog({ member, onClose }: { member: Member | null; onClose: () => void }) {
  const reset = useResetMemberPassword();
  const form = useFormik({
    initialValues: { new_password: "" },
    validationSchema: resetSchema,
    onSubmit: (v, helpers) => {
      if (!member) return;
      reset.mutate(
        { id: member.id, new_password: v.new_password },
        {
          onSuccess: () => {
            toast.success("Password reset");
            close();
          },
          onError: (e) => {
            const pw = passwordError(e, "new_password");
            if (pw) helpers.setFieldError("new_password", pw);
          },
        },
      );
    },
  });

  const close = () => {
    form.resetForm();
    onClose();
  };

  return (
    <Dialog
      open={!!member}
      onClose={close}
      title={`Reset password for ${member?.name ?? ""}`}
      description="They will be signed out everywhere and unlocked."
    >
      <form noValidate className="space-y-4" onSubmit={form.handleSubmit}>
        <Field
          label="New password"
          htmlFor="r-pw"
          error={fieldError(form, "new_password")}
          hint={PASSWORD_HINT}
        >
          <PasswordInput id="r-pw" autoComplete="new-password" {...bind(form, "new_password")} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" loading={reset.isPending}>
            Reset password
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { KeyRound, Loader2, Lock, ShieldOff, UserPlus } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { PasswordInput } from "@/components/auth/password-input";
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
import { ROLE_LABEL } from "@/lib/permissions";

const ROLES: Role[] = ["light_agent", "agent", "supervisor", "admin"];

export function MembersView() {
  const { user } = useCurrentUser();
  const q = useMembers();
  const update = useUpdateMember();
  const reset2fa = useResetMember2fa();
  const [adding, setAdding] = useState(false);
  const [resetFor, setResetFor] = useState<Member | null>(null);
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data]);

  return (
    <div>
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
                <li key={m.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                  <div className="min-w-0 flex-1 basis-52">
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
                        onClick={() => {
                          if (
                            !confirm(
                              `Turn off two-step sign-in for ${m.name}? They'll be signed out everywhere and set it up again at next sign-in.`,
                            )
                          )
                            return;
                          reset2fa.mutate(m.id, {
                            onSuccess: () => toast.success(`2FA reset for ${m.name}.`),
                          });
                        }}
                      >
                        <ShieldOff className="size-3.5" /> Reset 2FA
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={update.isPending}
                      onClick={() =>
                        update.mutate(
                          { id: m.id, is_active: !m.is_active },
                          // e.g. "the last active Admin can't be removed" comes back as the message.
                          {
                            onSuccess: () =>
                              toast.success(
                                m.is_active ? "Member deactivated" : "Member reactivated",
                              ),
                          },
                        )
                      }
                    >
                      {m.is_active ? "Deactivate" : "Reactivate"}
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {q.hasNextPage && (
          <div className="border-t border-border p-3 text-center">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => q.fetchNextPage()}
              disabled={q.isFetchingNextPage}
            >
              {q.isFetchingNextPage && <Loader2 className="size-4 animate-spin" />} Load more
            </Button>
          </div>
        )}
      </Card>

      <AddMemberDialog open={adding} onClose={() => setAdding(false)} />
      <ResetPasswordDialog member={resetFor} onClose={() => setResetFor(null)} />
    </div>
  );
}

const addSchema = z.object({
  name: z.string().trim().min(1, "Enter a name").max(120),
  email: z.string().trim().min(1, "Enter an email").email("Enter a valid email"),
  role: z.enum(["light_agent", "agent", "supervisor", "admin"]),
  password: z.string().min(10, "Use at least 10 characters").max(128, "Use at most 128 characters"),
});
type AddValues = z.infer<typeof addSchema>;

function AddMemberDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const add = useAddMember();
  const {
    register,
    control,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<AddValues>({ resolver: zodResolver(addSchema), defaultValues: { role: "agent" } });

  const close = () => {
    reset();
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Add member"
      description="Share the temporary password with them privately."
    >
      <form
        noValidate
        className="space-y-4"
        onSubmit={handleSubmit((v) =>
          add.mutate(v, {
            onSuccess: () => {
              toast.success(`${v.name} added`);
              close();
            },
            onError: (e) => {
              if (e instanceof ApiError && e.status === 422) {
                for (const d of e.details)
                  if (d.field in addSchema.shape)
                    setError(d.field as keyof AddValues, { message: d.message });
              }
            },
          }),
        )}
      >
        <Field label="Name" htmlFor="m-name" error={errors.name?.message}>
          <Input id="m-name" autoComplete="off" {...register("name")} />
        </Field>
        <Field label="Email" htmlFor="m-email" error={errors.email?.message}>
          <Input id="m-email" type="email" autoComplete="off" {...register("email")} />
        </Field>
        <Field label="Role" htmlFor="m-role">
          <FormSelect control={control} id="m-role" name="role">
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
          error={errors.password?.message}
          hint="10 to 128 characters."
        >
          <PasswordInput id="m-pw" autoComplete="new-password" {...register("password")} />
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

const resetSchema = z.object({
  new_password: z
    .string()
    .min(10, "Use at least 10 characters")
    .max(128, "Use at most 128 characters"),
});

function ResetPasswordDialog({ member, onClose }: { member: Member | null; onClose: () => void }) {
  const reset = useResetMemberPassword();
  const {
    register,
    handleSubmit,
    reset: resetForm,
    formState: { errors },
  } = useForm<z.infer<typeof resetSchema>>({ resolver: zodResolver(resetSchema) });

  const close = () => {
    resetForm();
    onClose();
  };

  return (
    <Dialog
      open={!!member}
      onClose={close}
      title={`Reset password for ${member?.name ?? ""}`}
      description="They will be signed out everywhere and unlocked."
    >
      <form
        noValidate
        className="space-y-4"
        onSubmit={handleSubmit((v) =>
          reset.mutate(
            { id: member!.id, new_password: v.new_password },
            {
              onSuccess: () => {
                toast.success("Password reset");
                close();
              },
            },
          ),
        )}
      >
        <Field label="New password" htmlFor="r-pw" error={errors.new_password?.message}>
          <PasswordInput id="r-pw" autoComplete="new-password" {...register("new_password")} />
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

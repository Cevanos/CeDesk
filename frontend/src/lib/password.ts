import * as yup from "yup";

import { ApiError } from "@/lib/api/client";

/**
 * Password length rules the browser can check on its own.
 *
 * Each workspace sets its own minimum in Security settings (8 to 64; 10 by default), and
 * only Admins can read that setting. So a form checks just the lowest minimum any workspace
 * can have, and the API enforces the workspace's real one: its 422 ("Use at least 14
 * characters.") is shown under the field with `passwordError`.
 */
export const PASSWORD_MIN_ANY = 8;
export const PASSWORD_MAX = 128;

export const PASSWORD_HINT = `At least ${PASSWORD_MIN_ANY} characters, or more if your workspace asks for it.`;

/** A new password, as far as the browser can tell. */
export const newPassword = () =>
  yup
    .string()
    .default("")
    .min(PASSWORD_MIN_ANY, `Use at least ${PASSWORD_MIN_ANY} characters`)
    .max(PASSWORD_MAX, `Use at most ${PASSWORD_MAX} characters`);

/** The API's 422 for a wrong current password: it names no field, only says so. */
const WRONG_CURRENT = /current password is not right/i;

/** A 422 with no field at all (the API's service rules raise those). */
function fieldless(e: unknown): e is ApiError {
  return e instanceof ApiError && e.status === 422 && e.details.length === 0 && !!e.message;
}

/** True when the API says the current password typed is wrong. */
export function isWrongCurrentPassword(e: unknown): boolean {
  return fieldless(e) && WRONG_CURRENT.test(e.message);
}

/**
 * The API's message about a new password, when a 422 is about it: a field error named
 * `field`, or a 422 with no field at all. A field-less message counts only when it is about
 * the length ("Use at least 14 characters."), so another rule's message (a role, a wrong
 * current password) never lands under the password box. `anyMessage`: for forms whose only
 * other field-less 422s are about the new password too (change password: "choose one
 * different from your current one"); a wrong current password is still left out.
 */
export function passwordError(
  e: unknown,
  field: string,
  { anyMessage = false }: { anyMessage?: boolean } = {},
): string | null {
  if (!(e instanceof ApiError) || e.status !== 422) return null;
  const named = e.details.find((d) => d.field === field);
  if (named) return named.message;
  if (!fieldless(e) || isWrongCurrentPassword(e)) return null;
  return anyMessage || /\bcharacters\b/i.test(e.message) ? e.message : null;
}

/** The change-password form: current password, a new one, and its confirmation. */
export const changePasswordSchema = yup.object({
  current_password: yup.string().default("").required("Enter your current password"),
  new_password: newPassword(),
  confirm: yup
    .string()
    .default("")
    .test("match", "Passwords do not match", function (v) {
      return v === this.parent.new_password;
    }),
});
export type ChangePasswordValues = yup.InferType<typeof changePasswordSchema>;

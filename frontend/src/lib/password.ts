import { z } from "zod";

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
  z
    .string()
    .min(PASSWORD_MIN_ANY, `Use at least ${PASSWORD_MIN_ANY} characters`)
    .max(PASSWORD_MAX, `Use at most ${PASSWORD_MAX} characters`);

/**
 * The API's message about a new password, when a 422 is about it: a field error named
 * `field`, or a 422 with no field at all (the password rules raise those).
 */
export function passwordError(e: unknown, field: string): string | null {
  if (!(e instanceof ApiError) || e.status !== 422) return null;
  const named = e.details.find((d) => d.field === field);
  if (named) return named.message;
  return e.details.length === 0 && e.message ? e.message : null;
}

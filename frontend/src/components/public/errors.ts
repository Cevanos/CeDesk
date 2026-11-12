import { ApiError } from "@/lib/api/client";

/**
 * Message for a customer-facing error. The backend writes `error.message` for the end
 * user, so 403, 410 and 429 are shown exactly as sent (the agent-side 429 wording
 * would hide the real reason, e.g. "Too many codes requested").
 */
export function publicErrorMessage(e: unknown): string {
  if (!(e instanceof ApiError)) return "Something went wrong. Please try again.";
  if ((e.status === 403 || e.status === 410 || e.status === 429) && e.message) return e.message;
  return e.userMessage;
}

/** Field message from a 422, if the server named that field. */
export function publicFieldError(e: unknown, field: string): string | undefined {
  return e instanceof ApiError ? e.fieldMessage(field) : undefined;
}

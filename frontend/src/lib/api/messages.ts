import { ApiError } from "@/lib/api/client";

/**
 * The text to show for a failed call. The inbox-tool routes write their messages for the agent
 * ("A resolved ticket can't be snoozed. Reopen it first."), so show them as they are. A 422 with
 * field details shows those first; rate limits and server faults keep the standard wording.
 */
export function apiMessage(e: unknown, fallback = "Something went wrong."): string {
  if (!(e instanceof ApiError)) return fallback;
  if (e.status === 429 || e.status >= 500 || e.status === 0) return e.userMessage;
  if (e.status === 422) {
    const d = e.details
      .map((x) => x.message)
      .filter(Boolean)
      .join(" ");
    if (d) return d;
  }
  return e.message || e.userMessage;
}

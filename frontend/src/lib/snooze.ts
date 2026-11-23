/** Quick snooze times, in the browser's time zone. Each `until` is an ISO string with `Z`. */
export interface SnoozePreset {
  id: "later" | "tomorrow" | "monday";
  label: string;
  until: string;
}

/** Local time `days` from today at 09:00. */
function at9(now: Date, days: number): Date {
  const d = new Date(now);
  d.setDate(d.getDate() + days);
  d.setHours(9, 0, 0, 0);
  return d;
}

export function snoozePresets(now: Date = new Date()): SnoozePreset[] {
  // Next Monday: always a future Monday, never today (so on a Monday it is 7 days ahead).
  const toMonday = (1 - now.getDay() + 7) % 7 || 7;
  return [
    {
      id: "later",
      label: "Later today",
      until: new Date(now.getTime() + 3 * 3600_000).toISOString(),
    },
    { id: "tomorrow", label: "Tomorrow 9:00", until: at9(now, 1).toISOString() },
    { id: "monday", label: "Next Monday 9:00", until: at9(now, toMonday).toISOString() },
  ];
}

/** The server needs at least a minute ahead and at most a year. */
export const SNOOZE_MIN_MS = 60_000;

/** Value for `<input type="datetime-local" min>` (local time, no zone, to the minute). */
export function localInputValue(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** A `datetime-local` value as an ISO time with a zone, or null when empty or not a date. */
export function localInputToIso(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.valueOf()) ? null : d.toISOString();
}

/** "21 Nov, 09:00": the same style as the rest of the ticket screens. */
export function formatSnoozeTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.valueOf())) return "";
  return d.toLocaleString([], {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Returns the URL only when it is an absolute http(s) link, otherwise `null`.
 *
 * Use it for every `href` that comes from data (API responses, user input) so a
 * `javascript:`, `data:` or `vbscript:` value can never reach an anchor.
 */
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  try {
    const { protocol, host } = new URL(trimmed);
    return (protocol === "https:" || protocol === "http:") && host ? trimmed : null;
  } catch {
    return null;
  }
}

/** A base that can't be a real site, to resolve `next` the way a browser would. */
const BASE = "https://cedesk.invalid";

/**
 * Only same-origin relative paths are valid post-login redirects.
 *
 * Browsers drop tabs and newlines from URLs, so "/\t/evil.example" would become
 * "//evil.example" (another site). Control characters and backslashes are refused, and the
 * path is resolved like a browser would to make sure it stays on this site.
 */
export function safeNext(next?: string | null, fallback = "/tickets") {
  if (!next || !next.startsWith("/") || /[\u0000-\u001f\u007f\\]/.test(next)) return fallback;
  let url: URL;
  try {
    url = new URL(next, BASE);
  } catch {
    return fallback;
  }
  if (url.origin !== BASE) return fallback;
  return url.pathname + url.search + url.hash;
}

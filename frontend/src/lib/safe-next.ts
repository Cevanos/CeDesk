/** Only same-origin relative paths are valid post-login redirects. */
export function safeNext(next?: string | null, fallback = "/tickets") {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) {
    return fallback;
  }
  return next;
}

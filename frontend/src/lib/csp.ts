export const TURNSTILE_ORIGIN = "https://challenges.cloudflare.com";

/**
 * CSP built per request, so scripts are allowed by a nonce instead of 'unsafe-inline'.
 *
 * connect-src 'self' keeps the browser talking only to this origin; the backend
 * is reached through the /api proxy so its address never shows up client side.
 *
 * style-src still needs 'unsafe-inline' for React style props and react-select's
 * emotion styles. Style injection is a much smaller risk than script injection.
 */
export function buildCsp(
  nonce: string,
  isDev: boolean,
  opts: { turnstile?: boolean } = {},
): string {
  // Only the sign-up page widens the policy, for the Cloudflare Turnstile CAPTCHA (ADR 0003).
  const ts = opts.turnstile ? ` ${TURNSTILE_ORIGIN}` : "";
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${ts}${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    opts.turnstile ? `frame-src ${TURNSTILE_ORIGIN}` : "frame-src 'none'",
    "worker-src 'self'",
    "manifest-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
    // Skipped only for plain-HTTP test servers (ALLOW_INSECURE_HTTP=1); otherwise the
    // browser rewrites asset URLs to https and CSS/JS fail to load without TLS.
    ...(isDev || process.env.ALLOW_INSECURE_HTTP === "1" ? [] : ["upgrade-insecure-requests"]),
  ].join("; ");
}

export function makeNonce(): string {
  return btoa(crypto.randomUUID());
}

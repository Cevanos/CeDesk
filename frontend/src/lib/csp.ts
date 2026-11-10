/**
 * Content-Security-Policy, built per request so scripts can be allowed by a
 * fresh nonce instead of `'unsafe-inline'`.
 *
 * `connect-src 'self'` is the important line: the browser may only talk to this
 * origin. The real backend is reached through the server-side proxy at /api/*,
 * so its address never appears in any bundle, network panel or CSP.
 *
 * `style-src 'unsafe-inline'` remains: React inline `style` props and the
 * emotion styles used by react-select need it, and style injection is a far
 * smaller risk than script injection.
 */
export function buildCsp(nonce: string, isDev: boolean): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "frame-src 'none'",
    "worker-src 'self'",
    "manifest-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
    ...(isDev ? [] : ["upgrade-insecure-requests"]),
  ].join("; ");
}

export function makeNonce(): string {
  return btoa(crypto.randomUUID());
}

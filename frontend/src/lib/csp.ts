/**
 * CSP built per request, so scripts are allowed by a nonce instead of 'unsafe-inline'.
 *
 * connect-src 'self' keeps the browser talking only to this origin; the backend
 * is reached through the /api proxy so its address never shows up client side.
 *
 * style-src still needs 'unsafe-inline' for React style props and react-select's
 * emotion styles. Style injection is a much smaller risk than script injection.
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

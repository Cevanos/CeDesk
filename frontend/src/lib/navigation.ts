/**
 * Full-page navigation. Used where a client-side transition would be wrong:
 * ending a session must drop every in-memory cache and React state, which only
 * a real document load guarantees.
 */
export function hardNavigate(url: string) {
  window.location.href = url;
}

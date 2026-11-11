/** Full page load, for when a client-side transition would keep stale caches (e.g. sign-out). */
export function hardNavigate(url: string) {
  window.location.href = url;
}

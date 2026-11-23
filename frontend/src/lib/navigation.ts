/**
 * Full page load, for when a client-side transition would keep stale caches (e.g. sign-out).
 * Only this site: a path that would resolve to another origin goes to "/" instead.
 */
export function hardNavigate(path: string) {
  const here = window.location.origin;
  const url = new URL(path, here);
  window.location.assign(url.origin === here ? url.pathname + url.search + url.hash : "/");
}

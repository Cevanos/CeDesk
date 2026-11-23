/**
 * In-memory fixed-window limiter for credential endpoints. Per instance, so with
 * several replicas the real limit is `limit x replicas`; the backend limiter is
 * still the authority.
 */
export interface Limiter {
  /** Returns `{ ok: true }` or `{ ok: false, retryAfter }` (seconds). */
  hit(key: string, now?: number): { ok: true } | { ok: false; retryAfter: number };
  reset(): void;
}

export function createLimiter(limit: number, windowMs: number, maxKeys = 10_000): Limiter {
  const buckets = new Map<string, { count: number; resetAt: number }>();

  const sweep = (now: number) => {
    for (const [k, b] of buckets) if (b.resetAt <= now) buckets.delete(k);
    // Hard cap so a flood of unique keys cannot grow memory without bound. Drop the oldest
    // windows first (Map keeps insertion order) rather than wiping every client's counter,
    // which would hand a flooder a free reset of its own limit.
    for (const k of buckets.keys()) {
      if (buckets.size < maxKeys) break;
      buckets.delete(k);
    }
  };

  return {
    hit(key, now = Date.now()) {
      if (buckets.size >= maxKeys) sweep(now);
      const b = buckets.get(key);
      if (!b || b.resetAt <= now) {
        buckets.set(key, { count: 1, resetAt: now + windowMs });
        return { ok: true };
      }
      b.count += 1;
      if (b.count > limit) return { ok: false, retryAfter: Math.ceil((b.resetAt - now) / 1000) };
      return { ok: true };
    },
    reset: () => buckets.clear(),
  };
}

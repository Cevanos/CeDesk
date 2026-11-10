/**
 * Small in-memory fixed-window limiter, used on credential endpoints to blunt
 * brute force. It is per server instance: behind several replicas the effective
 * limit is `limit x replicas`, so keep the backend's own limiter as the
 * authority. This is defence in depth, not the only line.
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
    // Hard cap so a flood of unique keys cannot grow memory without bound.
    if (buckets.size > maxKeys) buckets.clear();
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

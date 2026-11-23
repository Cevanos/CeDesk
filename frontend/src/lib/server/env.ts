import { z } from "zod";

/** Validated server env. No `server-only` import so instrumentation.ts can use it too. */
const schema = z.object({
  CEDESK_API_URL: z
    .string()
    .trim()
    .url("CEDESK_API_URL must be an absolute URL, e.g. https://api.example.com/api/v1")
    .refine((v) => /^https?:\/\//.test(v), "CEDESK_API_URL must use http(s)")
    .transform((v) => v.replace(/\/+$/, "")),
  /** Sign-in / password-reset attempts allowed per client per minute. */
  AUTH_RATE_LIMIT_PER_MINUTE: z.coerce.number().int().positive().max(100_000).default(10),
  /**
   * Reverse proxies / load balancers in front of this app that append to
   * X-Forwarded-For. Used to find the real client IP for rate limiting.
   * 0 = exposed directly (forwarded headers are ignored).
   */
  TRUSTED_PROXY_HOPS: z.coerce.number().int().min(0).max(10).default(1),
});

export type ServerEnv = z.infer<typeof schema>;

export type EnvResult = { ok: true; env: ServerEnv } | { ok: false; problems: string[] };

export function parseEnv(source: Record<string, string | undefined> = process.env): EnvResult {
  const parsed = schema.safeParse(source);
  if (parsed.success) return { ok: true, env: parsed.data };
  return {
    ok: false,
    problems: parsed.error.issues.map((i) => `${i.path.join(".") || "env"}: ${i.message}`),
  };
}

/** Throws with every problem listed. Called at boot so misconfiguration fails fast. */
export function assertEnv(source: Record<string, string | undefined> = process.env): ServerEnv {
  const r = parseEnv(source);
  if (!r.ok) {
    throw new Error(`Invalid environment:\n  - ${r.problems.join("\n  - ")}`);
  }
  return r.env;
}

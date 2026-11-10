import { NextResponse } from "next/server";

import { parseEnv } from "@/lib/server/env";

export const dynamic = "force-dynamic";

/**
 * Liveness/readiness probe for the container orchestrator. It reports whether
 * the server is configured; it does not call the backend, so a backend outage
 * never restarts the frontend.
 */
export function GET() {
  const env = parseEnv();
  return NextResponse.json(
    { status: env.ok ? "ok" : "misconfigured" },
    { status: env.ok ? 200 : 503, headers: { "cache-control": "no-store" } },
  );
}

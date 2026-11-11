import { NextResponse } from "next/server";

import { parseEnv } from "@/lib/server/env";

export const dynamic = "force-dynamic";

/** Health probe. Doesn't call the backend, so a backend outage won't restart the frontend. */
export function GET() {
  const env = parseEnv();
  return NextResponse.json(
    { status: env.ok ? "ok" : "misconfigured" },
    { status: env.ok ? 200 : 503, headers: { "cache-control": "no-store" } },
  );
}

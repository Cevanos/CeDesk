/** Runs once when the Next.js server starts. */
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { parseEnv } = await import("@/lib/server/env");
  const result = parseEnv();
  if (result.ok) return;
  const message = `Invalid environment:\n  - ${result.problems.join("\n  - ")}`;
  // Fail the production boot; in development just warn so `next dev` still starts.
  if (
    process.env.NODE_ENV === "production" &&
    process.env.NEXT_PHASE !== "phase-production-build"
  ) {
    throw new Error(message);
  }
  console.warn(message);
}

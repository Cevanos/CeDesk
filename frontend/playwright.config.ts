import { defineConfig, devices } from "@playwright/test";

const APP_PORT = 3100;
const API_PORT = 3101;

/**
 * End-to-end tests run the real Next.js app against a tiny in-repo mock backend
 * (e2e/mock-backend.mjs), so they need no external services and are repeatable.
 */
export default defineConfig({
  testDir: "./e2e",
  // Specs share one mock backend, so run them one at a time.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${APP_PORT}`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "node e2e/mock-backend.mjs",
      env: { PORT: String(API_PORT) },
      url: `http://localhost:${API_PORT}/health`,
      reuseExistingServer: !process.env.CI,
    },
    {
      // A production build: closest to what ships (nonce CSP, secure cookies, no HMR).
      // Its own dist dir means it never clashes with a running `next dev`.
      command: `npx cross-env NEXT_DIST_DIR=.next-e2e next build && npx cross-env NEXT_DIST_DIR=.next-e2e next start --port ${APP_PORT}`,
      env: {
        CDESK_API_URL: `http://localhost:${API_PORT}/api/v1`,
        // Every spec signs in from one IP; production keeps the default of 10.
        AUTH_RATE_LIMIT_PER_MINUTE: "10000",
      },
      url: `http://localhost:${APP_PORT}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
    },
  ],
});

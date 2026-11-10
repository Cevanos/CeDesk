import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const r = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": r("./src"),
      // `server-only` throws outside React Server Components; neutralise it in tests.
      "server-only": r("./src/test/server-only-stub.ts"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
    coverage: {
      provider: "v8",
      include: ["src/lib/**", "src/hooks/**", "src/app/api/**"],
      exclude: ["src/**/*.test.*", "src/test/**", "src/lib/api/schema.d.ts"],
      reporter: ["text", "lcov"],
    },
  },
});

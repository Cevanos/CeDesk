import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";

import { server } from "@/test/server";

// jsdom's fetch is Node's, which rejects relative URLs like "/api/me".
const nodeFetch = globalThis.fetch.bind(globalThis);
vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) =>
  typeof input === "string" && input.startsWith("/")
    ? nodeFetch(new URL(input, "http://localhost:3000"), init)
    : nodeFetch(input, init),
);

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  server.resetHandlers();
  cleanup();
});
afterAll(() => server.close());

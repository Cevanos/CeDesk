import { describe, expect, it } from "vitest";

import { assertEnv, parseEnv } from "@/lib/server/env";

describe("parseEnv", () => {
  it("accepts an https URL and trims trailing slashes", () => {
    const r = parseEnv({ CDESK_API_URL: " https://api.example.com/api/v1/// " });
    expect(r).toEqual({
      ok: true,
      env: {
        CDESK_API_URL: "https://api.example.com/api/v1",
        AUTH_RATE_LIMIT_PER_MINUTE: 10,
        TRUSTED_PROXY_HOPS: 1,
      },
    });
  });

  it("rejects a negative or absurd TRUSTED_PROXY_HOPS", () => {
    for (const v of ["-1", "11", "abc"]) {
      expect(parseEnv({ CDESK_API_URL: "https://a.example/api", TRUSTED_PROXY_HOPS: v }).ok).toBe(
        false,
      );
    }
  });

  it("accepts http for local development", () => {
    expect(parseEnv({ CDESK_API_URL: "http://localhost:8000/api/v1" }).ok).toBe(true);
  });

  it.each([undefined, "", "not a url", "ftp://host/x"])("rejects %j", (v) => {
    expect(parseEnv({ CDESK_API_URL: v }).ok).toBe(false);
  });

  it("explains the problem", () => {
    const r = parseEnv({});
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.problems[0]).toContain("CDESK_API_URL");
  });
});

describe("AUTH_RATE_LIMIT_PER_MINUTE", () => {
  const url = "https://a.b/c";
  it("is configurable", () => {
    const r = parseEnv({ CDESK_API_URL: url, AUTH_RATE_LIMIT_PER_MINUTE: "500" });
    expect(r.ok && r.env.AUTH_RATE_LIMIT_PER_MINUTE).toBe(500);
  });
  it.each(["0", "-1", "abc", "1.5"])("rejects %s", (v) =>
    expect(parseEnv({ CDESK_API_URL: url, AUTH_RATE_LIMIT_PER_MINUTE: v }).ok).toBe(false),
  );
});

describe("assertEnv", () => {
  it("throws listing every problem", () =>
    expect(() => assertEnv({})).toThrow(/Invalid environment/));
  it("returns the parsed env", () =>
    expect(assertEnv({ CDESK_API_URL: "https://a.b/c" }).CDESK_API_URL).toBe("https://a.b/c"));
});

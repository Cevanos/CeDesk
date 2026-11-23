import { describe, expect, it } from "vitest";

import {
  BodyTooLargeError,
  clientKey,
  isAllowedTarget,
  isSameOrigin,
  readBodyCapped,
  safeRedirectLocation,
  visitorIp,
} from "@/lib/server/proxy-guards";

const allowed = new Set(["tickets", "me"]);
const h = (o: Record<string, string>) => new Headers(o);

describe("visitorIp", () => {
  it("returns exactly the one address our proxy added", () =>
    expect(visitorIp(h({ "x-forwarded-for": "1.2.3.4, 203.0.113.9" }), 1)).toBe("203.0.113.9"));
  it("returns nothing with 0 hops, even if the visitor sent one", () =>
    expect(visitorIp(h({ "x-forwarded-for": "1.2.3.4" }), 0)).toBeNull());
  it("returns nothing for a value that is not an IP", () => {
    expect(visitorIp(h({ "x-forwarded-for": "not-an-ip" }), 1)).toBeNull();
    expect(visitorIp(h({}), 1)).toBeNull();
  });
  it("never returns more than one address", () =>
    expect(visitorIp(h({ "x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3" }), 2)).toBe("2.2.2.2"));
  it("accepts IPv6", () =>
    expect(visitorIp(h({ "x-forwarded-for": "2001:db8::1" }), 1)).toBe("2001:db8::1"));
});

describe("isAllowedTarget", () => {
  it("allows known areas", () => {
    expect(isAllowedTarget(["tickets"], allowed)).toBe(true);
    expect(isAllowedTarget(["tickets", "abc-123", "messages"], allowed)).toBe(true);
  });
  it("rejects unknown areas and empty paths", () => {
    expect(isAllowedTarget(["admin"], allowed)).toBe(false);
    expect(isAllowedTarget([], allowed)).toBe(false);
  });
  it.each([["."], [".."], ["a b"], ["a/b"], ["%2e%2e"], ["x?y"], ["a\\b"], [""]])(
    "rejects traversal and odd segment %j",
    (seg) => expect(isAllowedTarget(["tickets", seg as string], allowed)).toBe(false),
  );
});

describe("isSameOrigin", () => {
  it("accepts a matching Origin", () =>
    expect(isSameOrigin(h({ origin: "https://app.test" }), "app.test")).toBe(true));
  it("rejects a foreign Origin", () =>
    expect(isSameOrigin(h({ origin: "https://evil.test" }), "app.test")).toBe(false));
  it("rejects a malformed Origin", () =>
    expect(isSameOrigin(h({ origin: "nope" }), "app.test")).toBe(false));
  it("without Origin, relies on fetch metadata", () => {
    expect(isSameOrigin(h({}), "app.test")).toBe(true);
    expect(isSameOrigin(h({ "sec-fetch-site": "same-origin" }), "app.test")).toBe(true);
    expect(isSameOrigin(h({ "sec-fetch-site": "none" }), "app.test")).toBe(true);
    expect(isSameOrigin(h({ "sec-fetch-site": "cross-site" }), "app.test")).toBe(false);
  });
  it("refuses a sibling subdomain (same-site, not same-origin)", () =>
    expect(isSameOrigin(h({ "sec-fetch-site": "same-site" }), "app.test")).toBe(false));
});

const streamOf = (...parts: number[]) =>
  new ReadableStream<Uint8Array>({
    start(c) {
      for (const n of parts) c.enqueue(new Uint8Array(n).fill(1));
      c.close();
    },
  });

describe("readBodyCapped", () => {
  it("returns an empty body for no stream", async () =>
    expect((await readBodyCapped(null, 10)).byteLength).toBe(0));
  it("joins chunks up to the limit", async () =>
    expect((await readBodyCapped(streamOf(4, 6), 10)).byteLength).toBe(10));
  it("stops and throws once the real size passes the limit", async () =>
    await expect(readBodyCapped(streamOf(6, 6), 10)).rejects.toBeInstanceOf(BodyTooLargeError));
});

describe("safeRedirectLocation", () => {
  it("keeps an absolute https URL", () =>
    expect(safeRedirectLocation("https://idp.example.com/sso?x=1")).toBe(
      "https://idp.example.com/sso?x=1",
    ));
  it("allows http only when asked", () => {
    expect(safeRedirectLocation("http://idp.test/sso")).toBeNull();
    expect(safeRedirectLocation("http://idp.test/sso", true)).toBe("http://idp.test/sso");
  });
  it.each(["javascript:alert(1)", "//evil.test", "/relative", "data:text/html,x", "", null])(
    "drops %j",
    (v) => expect(safeRedirectLocation(v)).toBeNull(),
  );
});

describe("clientKey", () => {
  it("trusts only the entry our proxy appended, not a client-forged one", () =>
    expect(clientKey(h({ "x-forwarded-for": "6.6.6.6, 1.2.3.4" }))).toBe("1.2.3.4"));
  it("counts hops from the right when several proxies are trusted", () =>
    expect(clientKey(h({ "x-forwarded-for": "6.6.6.6, 1.2.3.4, 10.0.0.1" }), 2)).toBe("1.2.3.4"));
  it("never reads past the start of the list", () =>
    expect(clientKey(h({ "x-forwarded-for": "1.2.3.4" }), 3)).toBe("1.2.3.4"));
  it("ignores forwarded headers entirely with zero trusted hops", () =>
    expect(clientKey(h({ "x-forwarded-for": "6.6.6.6", "x-real-ip": "7.7.7.7" }), 0)).toBe(
      "direct",
    ));
  it("falls back to x-real-ip then unknown", () => {
    expect(clientKey(h({ "x-real-ip": "9.9.9.9" }))).toBe("9.9.9.9");
    expect(clientKey(h({}))).toBe("unknown");
  });
});

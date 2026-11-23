import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  api,
  createIdempotency,
  listOf,
  qs,
  request,
  setUnauthorizedHandler,
} from "@/lib/api/client";
import { server } from "@/test/server";

const URL_BASE = "http://localhost:3000/api";

afterEach(() => setUnauthorizedHandler(null));

describe("qs", () => {
  it("drops empty values and encodes the rest", () => {
    expect(qs({ a: "x y", b: "", c: undefined, d: null, e: 0, f: false })).toBe(
      "?a=x+y&e=0&f=false",
    );
  });
  it("is empty when there is nothing to send", () => {
    expect(qs()).toBe("");
    expect(qs({ a: "" })).toBe("");
  });
});

describe("listOf", () => {
  it("accepts a bare array", () => expect(listOf([1, 2])).toEqual([1, 2]));
  it.each(["items", "tickets", "members", "teams"])("reads the %s key", (k) =>
    expect(listOf({ [k]: [1] })).toEqual([1]),
  );
  it("falls back to the first array value", () => expect(listOf({ n: 3, rows: [9] })).toEqual([9]));
  it("is empty for anything else", () => {
    expect(listOf(null)).toEqual([]);
    expect(listOf({ a: 1 })).toEqual([]);
  });
});

describe("ApiError.userMessage", () => {
  const mk = (status: number, extra: Partial<ConstructorParameters<typeof ApiError>[0]> = {}) =>
    new ApiError({ status, code: "X", message: "server said", ...extra });

  it("explains a conflict", () => expect(mk(409).userMessage).toMatch(/Someone else changed this/));
  it("shows the server message for a conflict on a request without If-Match", () => {
    const e = mk(409, { message: "Accept the chat first." });
    e.specificConflict = true;
    expect(e.userMessage).toBe("Accept the chat first.");
  });
  it("shows the server message for 503", () =>
    expect(mk(503, { message: "Cevanos can't be reached now" }).userMessage).toBe(
      "Cevanos can't be reached now",
    ));
  it("includes the wait for 429", () =>
    expect(mk(429, { retryAfter: 12 }).userMessage).toContain("12 seconds"));
  it("includes the request id for 500", () =>
    expect(mk(500, { requestId: "abc" }).userMessage).toContain("abc"));
  it("passes other messages through", () => expect(mk(422).userMessage).toBe("server said"));
  it("finds a field message", () =>
    expect(mk(422, { details: [{ field: "email", message: "bad" }] }).fieldMessage("email")).toBe(
      "bad",
    ));
});

describe("request", () => {
  it("returns data and the ETag", async () => {
    server.use(
      http.get(`${URL_BASE}/tickets/1`, () =>
        HttpResponse.json({ id: "1" }, { headers: { etag: '"v3"' } }),
      ),
    );
    await expect(request("tickets/1")).resolves.toEqual({ data: { id: "1" }, etag: '"v3"' });
  });

  it("handles 204 with no body", async () => {
    server.use(
      http.post(`${URL_BASE}/auth/sign-out`, () => new HttpResponse(null, { status: 204 })),
    );
    await expect(api.post("auth/sign-out")).resolves.toBeUndefined();
  });

  it("sends JSON bodies and If-Match", async () => {
    let seen: { body: unknown; ifMatch: string | null } | undefined;
    server.use(
      http.patch(`${URL_BASE}/tickets/1`, async ({ request: r }) => {
        seen = { body: await r.json(), ifMatch: r.headers.get("if-match") };
        return HttpResponse.json({});
      }),
    );
    await request("tickets/1", {
      method: "PATCH",
      body: { status: "pending" },
      headers: { "If-Match": '"v1"' },
    });
    expect(seen).toEqual({ body: { status: "pending" }, ifMatch: '"v1"' });
  });

  it("maps the error envelope", async () => {
    server.use(
      http.post(`${URL_BASE}/tickets`, () =>
        HttpResponse.json(
          {
            error: {
              code: "Validation",
              message: "Check the form",
              details: [{ field: "subject", message: "required" }],
            },
          },
          { status: 422, headers: { "x-request-id": "req-9" } },
        ),
      ),
    );
    const err = await request("tickets", { method: "POST", body: {} }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 422, code: "Validation", requestId: "req-9" });
    expect(err.fieldMessage("subject")).toBe("required");
  });

  it("survives a non-JSON error body", async () => {
    server.use(http.get(`${URL_BASE}/me`, () => new HttpResponse("<html>", { status: 502 })));
    const err = await request("me").catch((e) => e);
    expect(err).toMatchObject({ status: 502, code: "Internal" });
  });

  it("reads Retry-After", async () => {
    server.use(
      http.get(`${URL_BASE}/me`, () =>
        HttpResponse.json(
          { error: { code: "RateLimited", message: "slow" } },
          { status: 429, headers: { "retry-after": "30" } },
        ),
      ),
    );
    const err = await request("me").catch((e) => e);
    expect(err.retryAfter).toBe(30);
  });

  it("signals the session handler on 401", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    server.use(
      http.get(`${URL_BASE}/me`, () =>
        HttpResponse.json({ error: { code: "NotSignedIn", message: "x" } }, { status: 401 }),
      ),
    );
    await request("me").catch(() => {});
    expect(handler).toHaveBeenCalledOnce();
  });

  it("does not treat a failed sign-in as an ended session", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    server.use(
      http.post(`${URL_BASE}/auth/sign-in`, () =>
        HttpResponse.json({ error: { code: "Bad", message: "no" } }, { status: 401 }),
      ),
    );
    await request("auth/sign-in", { method: "POST", body: {} }).catch(() => {});
    expect(handler).not.toHaveBeenCalled();
  });

  it("reports a network failure as status 0", async () => {
    server.use(http.get(`${URL_BASE}/me`, () => HttpResponse.error()));
    const err = await request("me").catch((e) => e);
    expect(err).toMatchObject({ status: 0, code: "Network" });
  });
});

describe("createIdempotency", () => {
  it("reuses one key until the action is done, then makes a fresh one", () => {
    const idem = createIdempotency();
    const first = idem.header()["Idempotency-Key"];
    expect(first).toMatch(/^[0-9a-f-]{36}$/);
    expect(idem.header()["Idempotency-Key"]).toBe(first);
    idem.done();
    expect(idem.header()["Idempotency-Key"]).not.toBe(first);
  });
});

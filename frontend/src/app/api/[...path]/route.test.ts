// @vitest-environment node
import { http, HttpResponse } from "msw";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { GET, POST } from "@/app/api/[...path]/route";
import { server } from "@/test/server";

const BACKEND = "https://backend.test/api/v1";
const ORIGIN = "http://localhost:3000";

function call(
  handler: typeof GET,
  path: string[],
  init: { method?: string; headers?: Record<string, string>; body?: string } = {},
) {
  const req = new NextRequest(`${ORIGIN}/api/${path.join("/")}`, {
    method: init.method ?? "GET",
    headers: init.headers,
    body: init.body,
  });
  return handler(req, { params: Promise.resolve({ path }) });
}

const session = { cookie: "cdesk_session=tok-123" };
let ipCounter = 0;
const freshIp = () => ({ "x-forwarded-for": `10.0.0.${++ipCounter}` });

beforeEach(() => {
  process.env.CDESK_API_URL = BACKEND;
});
afterEach(() => {
  delete process.env.CDESK_API_URL;
});

describe("Phase 1 areas and headers", () => {
  it("allows the new areas and forwards Idempotency-Key both ways", async () => {
    let seen: string | null = null;
    server.use(
      http.post(`${BACKEND}/tickets/abc/accept`, ({ request }) => {
        seen = request.headers.get("idempotency-key");
        return HttpResponse.json({}, { headers: { "Idempotent-Replayed": "true" } });
      }),
      http.get(`${BACKEND}/sla`, () => HttpResponse.json({ targets: [] })),
    );
    const res = await call(POST, ["tickets", "abc", "accept"], {
      method: "POST",
      headers: { ...session, "idempotency-key": "k-1" },
    });
    expect(seen).toBe("k-1");
    expect(res.headers.get("idempotent-replayed")).toBe("true");
    expect((await call(GET, ["sla"], { headers: session })).status).toBe(200);
  });
});

describe("target validation", () => {
  it("404s areas that are not allow-listed", async () => {
    const res = await call(GET, ["admin", "secrets"], { headers: session });
    expect(res.status).toBe(404);
  });

  it("404s path traversal", async () => {
    const res = await call(GET, ["tickets", ".."], { headers: session });
    expect(res.status).toBe(404);
  });

  it("blocks cross-site mutations", async () => {
    const res = await call(POST, ["tickets"], {
      method: "POST",
      headers: { ...session, origin: "https://evil.test" },
      body: "{}",
    });
    expect(res.status).toBe(403);
  });

  it("returns 503 when the backend is not configured, without naming it", async () => {
    delete process.env.CDESK_API_URL;
    const res = await call(GET, ["me"], { headers: session });
    expect(res.status).toBe(503);
    expect(await res.text()).not.toContain("backend");
  });
});

describe("session handling", () => {
  it("returns 401 and clears cookies when there is no session", async () => {
    const res = await call(GET, ["me"]);
    expect(res.status).toBe(401);
    expect(res.headers.get("set-cookie")).toContain("cdesk_session=;");
  });

  it("attaches the bearer token and drops browser-only headers", async () => {
    let seen: Headers | undefined;
    server.use(
      http.get(`${BACKEND}/me`, ({ request }) => {
        seen = request.headers;
        return HttpResponse.json({ id: "m1" }, { headers: { etag: '"e1"', "x-secret": "no" } });
      }),
    );
    const res = await call(GET, ["me"], {
      headers: { ...session, "x-forwarded-for": "1.1.1.1", accept: "application/json" },
    });
    expect(res.status).toBe(200);
    expect(seen?.get("authorization")).toBe("Bearer tok-123");
    expect(seen?.get("cookie")).toBeNull();
    expect(seen?.get("x-forwarded-for")).toBeNull();
    expect(res.headers.get("etag")).toBe('"e1"');
    expect(res.headers.get("x-secret")).toBeNull();
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("clears the session when the backend says 401", async () => {
    server.use(http.get(`${BACKEND}/me`, () => HttpResponse.json({}, { status: 401 })));
    const res = await call(GET, ["me"], { headers: session });
    expect(res.status).toBe(401);
    expect(res.headers.get("set-cookie")).toContain("cdesk_session=;");
  });
});

describe("sign-in", () => {
  const signIn = (ip = freshIp()) =>
    call(POST, ["auth", "sign-in"], {
      method: "POST",
      headers: { origin: ORIGIN, "content-type": "application/json", ...ip },
      body: JSON.stringify({ workspace: "w", email: "a@b.c", password: "x" }),
    });

  it("moves the token into an httpOnly cookie and out of the body", async () => {
    server.use(
      http.post(`${BACKEND}/auth/sign-in`, () =>
        HttpResponse.json({
          token: "secret-token",
          member_id: "m1",
          role: "agent",
          expires_at: "2099-01-01T00:00:00Z",
        }),
      ),
    );
    const res = await signIn();
    const body = await res.text();
    const cookie = res.headers.get("set-cookie") ?? "";
    expect(res.status).toBe(200);
    expect(body).not.toContain("secret-token");
    expect(JSON.parse(body)).toMatchObject({ member_id: "m1", role: "agent" });
    expect(cookie).toContain("cdesk_session=secret-token");
    expect(cookie.toLowerCase()).toContain("httponly");
    expect(cookie.toLowerCase()).toContain("samesite=lax");
  });

  it("rejects an upstream sign-in response with no token", async () => {
    server.use(http.post(`${BACKEND}/auth/sign-in`, () => HttpResponse.json({ ok: true })));
    expect((await signIn()).status).toBe(502);
  });

  it("rate limits repeated attempts from one client", async () => {
    server.use(
      http.post(`${BACKEND}/auth/sign-in`, () =>
        HttpResponse.json({ error: { code: "Bad", message: "no" } }, { status: 401 }),
      ),
    );
    const ip = freshIp();
    for (let i = 0; i < 10; i++) expect((await signIn(ip)).status).toBe(401);
    const blocked = await signIn(ip);
    expect(blocked.status).toBe(429);
    expect(Number(blocked.headers.get("retry-after"))).toBeGreaterThan(0);
    // another client is unaffected
    expect((await signIn()).status).toBe(401);
  });
});

describe("failure handling", () => {
  it("returns 502 without leaking the upstream address", async () => {
    server.use(http.get(`${BACKEND}/me`, () => HttpResponse.error()));
    const res = await call(GET, ["me"], { headers: session });
    expect(res.status).toBe(502);
    expect(await res.text()).not.toContain("backend.test");
  });

  it("rejects uploads over 25 MB before forwarding", async () => {
    const res = await call(POST, ["tickets", "t1", "attachments"], {
      method: "POST",
      headers: { ...session, origin: ORIGIN, "content-length": String(26 * 1024 * 1024) },
      body: "x",
    });
    expect(res.status).toBe(413);
  });
});

describe("customer portal and public areas", () => {
  const portalCookie = { cookie: "cdesk_portal=portal-tok" };
  const post = (path: string[], headers: Record<string, string> = {}, body = "{}") =>
    call(POST, path, {
      method: "POST",
      headers: { origin: ORIGIN, "content-type": "application/json", ...freshIp(), ...headers },
      body,
    });

  it("sends the portal cookie to /portal and never the agent token", async () => {
    let auth: string | null = null;
    server.use(
      http.get(`${BACKEND}/portal/tickets`, ({ request }) => {
        auth = request.headers.get("authorization");
        return HttpResponse.json({ items: [] });
      }),
    );
    const res = await call(GET, ["portal", "tickets"], {
      headers: { cookie: "cdesk_session=agent-tok; cdesk_portal=portal-tok" },
    });
    expect(res.status).toBe(200);
    expect(auth).toBe("Bearer portal-tok");
  });

  it("does not let an agent session open portal routes", async () => {
    const res = await call(GET, ["portal", "tickets"], { headers: session });
    expect(res.status).toBe(401);
    // a portal 401 never clears the agent cookie
    expect(res.headers.get("set-cookie") ?? "").not.toContain("cdesk_session");
  });

  it("does not send the portal token to agent routes", async () => {
    const res = await call(GET, ["me"], { headers: portalCookie });
    expect(res.status).toBe(401);
  });

  it("calls public routes without any token", async () => {
    let auth: string | null = "unset";
    server.use(
      http.get(`${BACKEND}/public/acme/articles`, ({ request }) => {
        auth = request.headers.get("authorization");
        return HttpResponse.json({ articles: [] });
      }),
    );
    const res = await call(GET, ["public", "acme", "articles"], {
      headers: { cookie: "cdesk_session=agent-tok; cdesk_portal=portal-tok" },
    });
    expect(res.status).toBe(200);
    expect(auth).toBeNull();
  });

  it("moves the portal token into an httpOnly cookie and out of the body", async () => {
    server.use(
      http.post(`${BACKEND}/portal/acme/session`, () =>
        HttpResponse.json({
          token: "portal-secret",
          email: "a@b.c",
          expires_at: "2099-01-01T00:00:00Z",
        }),
      ),
    );
    const res = await post(["portal", "acme", "session"]);
    const body = await res.text();
    const cookie = res.headers.get("set-cookie") ?? "";
    expect(res.status).toBe(200);
    expect(body).not.toContain("portal-secret");
    expect(JSON.parse(body)).toMatchObject({ email: "a@b.c" });
    expect(cookie).toContain("cdesk_portal=portal-secret");
    expect(cookie.toLowerCase()).toContain("httponly");
    expect(cookie).not.toContain("cdesk_session");
  });

  it("clears only the portal cookie on portal sign-out", async () => {
    server.use(
      http.post(`${BACKEND}/portal/sign-out`, () => new HttpResponse(null, { status: 204 })),
    );
    const res = await post(["portal", "sign-out"], portalCookie);
    const cookie = res.headers.get("set-cookie") ?? "";
    expect(res.status).toBe(204);
    expect(cookie).toContain("cdesk_portal=;");
    expect(cookie).not.toContain("cdesk_session");
  });

  it("clears the portal cookie when the backend says the portal token is dead", async () => {
    server.use(http.get(`${BACKEND}/portal/tickets`, () => HttpResponse.json({}, { status: 401 })));
    const res = await call(GET, ["portal", "tickets"], { headers: portalCookie });
    expect(res.status).toBe(401);
    expect(res.headers.get("set-cookie")).toContain("cdesk_portal=;");
  });

  it("rate limits asking for sign-in codes per client", async () => {
    server.use(
      http.post(`${BACKEND}/portal/acme/code`, () => new HttpResponse(null, { status: 204 })),
    );
    const ip = freshIp();
    for (let i = 0; i < 10; i++) {
      expect((await post(["portal", "acme", "code"], ip)).status).toBe(204);
    }
    expect((await post(["portal", "acme", "code"], ip)).status).toBe(429);
  });
});

describe("Phase 2 areas", () => {
  it("forwards imports calls with the raw CSV body and Idempotency-Key", async () => {
    let seen: { type: string | null; key: string | null; body: string } | null = null;
    server.use(
      http.post(`${BACKEND}/imports/csv`, async ({ request }) => {
        seen = {
          type: request.headers.get("content-type"),
          key: request.headers.get("idempotency-key"),
          body: await request.text(),
        };
        return HttpResponse.json({ id: "c1" }, { status: 201 });
      }),
    );
    const res = await call(POST, ["imports", "csv"], {
      method: "POST",
      headers: { ...session, "content-type": "text/csv", "idempotency-key": "k-2" },
      body: "a,b\n1,2",
    });
    expect(res.status).toBe(201);
    expect(seen).toEqual({ type: "text/csv", key: "k-2", body: "a,b\n1,2" });
  });

  it("streams events through unbuffered and keeps the session on a refused ticket", async () => {
    server.use(
      http.get(`${BACKEND}/stream`, ({ request }) =>
        new URL(request.url).searchParams.get("ticket") === "good"
          ? new HttpResponse("event: reset\ndata: {}\n\n", {
              headers: { "content-type": "text/event-stream" },
            })
          : HttpResponse.json({ error: { code: "Unauthorized", message: "x" } }, { status: 401 }),
      ),
    );
    const ok = await new NextRequest(`${ORIGIN}/api/stream?ticket=good`, { headers: session });
    const okRes = await GET(ok, { params: Promise.resolve({ path: ["stream"] }) });
    const bad = new NextRequest(`${ORIGIN}/api/stream?ticket=bad`, { headers: session });
    const badRes = await GET(bad, { params: Promise.resolve({ path: ["stream"] }) });
    expect(badRes.status).toBe(401);
    expect(badRes.headers.get("set-cookie")).toBeNull();
    expect(okRes.status).toBe(200);
    expect(okRes.headers.get("content-type")).toBe("text/event-stream");
    expect(okRes.headers.get("cache-control")).toContain("no-transform");
  });
});

// A tiny stand-in for the CDesk API, just enough for the end-to-end tests.
// Run by Playwright (see playwright.config.ts). Not part of the app bundle.
import { createServer } from "node:http";

const PORT = Number(process.env.PORT ?? 3101);
const TOKEN = "mock-token";
const PASSWORD = "correct-horse";

const me = {
  id: "m1",
  name: "Ada Admin",
  email: "ada@example.com",
  role: "admin",
  workspace: { name: "Acme", slug: "acme" },
  status: "available",
};

let seq = 1002;
/** @type {Record<string, any>} */
let tickets = seedTickets();

function seedTickets() {
  seq = 1002;
  return { t1: ticket("t1", "T-1001", "Cannot log in", "high", "open") };
}

function ticket(id, label, subject, priority, status) {
  return {
    id,
    label,
    subject,
    status,
    priority,
    channel: "email",
    type_name: null,
    team_name: null,
    assignee_name: null,
    assignee_id: null,
    team_id: null,
    type_id: null,
    contact: { name: "Grace Hopper", email: "grace@example.com" },
    tags: [],
    created_at: "2026-01-01T10:00:00Z",
    updated_at: "2026-01-01T11:00:00Z",
    messages: [],
    older_messages_cursor: null,
    handover: null,
    version: 1,
  };
}

const summary = ({ messages: _m, handover: _h, version: _v, ...rest }) => rest;

function send(res, status, body, headers = {}) {
  res.writeHead(status, { "content-type": "application/json", ...headers });
  res.end(body === undefined ? undefined : JSON.stringify(body));
}
const fail = (res, status, code, message) => send(res, status, { error: { code, message } });

async function readJson(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const raw = Buffer.concat(chunks).toString();
  return raw ? JSON.parse(raw) : {};
}

let signup = null;
const signupAck = () => ({
  signup_id: "00000000-0000-4000-8000-000000000001",
  code_expires_at: new Date(Date.now() + 600_000).toISOString(),
  resend_after: new Date(Date.now() + 60_000).toISOString(),
});

createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://localhost:${PORT}`);
  if (url.pathname === "/health") return send(res, 200, { ok: true });
  if (url.pathname === "/__reset" && req.method === "POST") {
    tickets = seedTickets();
    signup = null;
    return send(res, 200, { ok: true });
  }

  const path = url.pathname
    .replace(/^\/api\/v1\//, "")
    .split("/")
    .filter(Boolean);
  const [area, id, sub] = path;
  const method = req.method ?? "GET";

  if (area === "auth" && id === "sign-in" && method === "POST") {
    const body = await readJson(req);
    if (body.password !== PASSWORD) return fail(res, 401, "InvalidCredentials", "Bad credentials");
    return send(res, 200, {
      token: TOKEN,
      member_id: me.id,
      role: me.role,
      expires_at: new Date(Date.now() + 8 * 3600_000).toISOString(),
    });
  }
  if (area === "auth" && id === "sign-out") return send(res, 204);

  // Self-service sign-up. The code is always 123456; no CAPTCHA (site key null).
  if (area === "auth" && id === "signup-config" && method === "GET")
    return send(res, 200, { enabled: true, captcha_site_key: null });
  if (area === "auth" && id === "signup" && !sub && method === "POST") {
    const body = await readJson(req);
    if (body.workspace === "taken") return fail(res, 409, "slug_taken", "That address is taken.");
    signup = { workspace: body.workspace };
    return send(res, 202, signupAck());
  }
  if (area === "auth" && id === "signup" && sub === "verify" && method === "POST") {
    const body = await readJson(req);
    if (!signup) return fail(res, 410, "signup_expired", "Start again.");
    if (body.code !== "123456") return fail(res, 422, "bad_code", "Wrong code.");
    return send(res, 200, { workspace: body.workspace ?? signup.workspace });
  }
  if (area === "auth" && id === "signup" && sub === "resend" && method === "POST")
    return send(res, 202, signupAck());

  if (req.headers.authorization !== `Bearer ${TOKEN}`) {
    return fail(res, 401, "NotSignedIn", "Please sign in.");
  }

  if (area === "me" && !id) return send(res, 200, me);
  if (area === "notifications") return send(res, 200, { items: [], unread: 0 });
  if (["teams", "ticket-types", "agents"].includes(area ?? ""))
    return send(res, 200, { items: [] });
  if (area === "members")
    return send(res, 200, { items: [{ ...me, is_active: true }], next_after: null });
  if (area === "routing-defaults")
    return send(res, 200, { cap_email: 5, cap_chat: 3, cap_messaging: 3 });

  if (area === "tickets") {
    if (!id && method === "GET") {
      const status = url.searchParams.get("status") ?? "open";
      const all = Object.values(tickets);
      const counts = Object.fromEntries(
        ["open", "pending", "resolved"].map((s) => [s, all.filter((t) => t.status === s).length]),
      );
      return send(res, 200, {
        items: all.filter((t) => t.status === status).map(summary),
        next_cursor: null,
        counts,
      });
    }
    if (!id && method === "POST") {
      const body = await readJson(req);
      if (!body.subject) {
        return send(res, 422, {
          error: {
            code: "Validation",
            message: "Invalid",
            details: [{ field: "subject", message: "required" }],
          },
        });
      }
      const key = `t${seq}`;
      tickets[key] = {
        ...ticket(key, `T-${seq}`, body.subject, body.priority ?? "medium", "open"),
        contact: body.contact,
      };
      seq += 1;
      return send(res, 201, tickets[key], { etag: '"1"' });
    }
    const t = id ? tickets[id] : undefined;
    if (!t) return fail(res, 404, "NotFound", "No such ticket");
    const etag = `"${t.version}"`;
    if (!sub && method === "GET") return send(res, 200, t, { etag });
    if (!sub && method === "PATCH") {
      if (req.headers["if-match"] && req.headers["if-match"] !== etag) {
        return fail(res, 409, "Conflict", "Changed elsewhere");
      }
      Object.assign(t, await readJson(req));
      t.version += 1;
      return send(res, 200, t, { etag: `"${t.version}"` });
    }
    if (sub === "resolve" && method === "POST") {
      t.status = "resolved";
      t.version += 1;
      return send(res, 200, t, { etag: `"${t.version}"` });
    }
    if (sub === "messages" && method === "POST") {
      const body = await readJson(req);
      t.messages.push({
        id: `msg${t.messages.length + 1}`,
        kind: body.kind,
        body: body.body,
        author_member_id: me.id,
        author_name: me.name,
        created_at: new Date().toISOString(),
      });
      return send(res, 201, {});
    }
    if (sub === "attachments") return send(res, 200, { items: [] });
  }

  return fail(res, 404, "NotFound", `No mock for ${method} ${url.pathname}`);
}).listen(PORT, () => console.log(`mock backend on :${PORT}`));

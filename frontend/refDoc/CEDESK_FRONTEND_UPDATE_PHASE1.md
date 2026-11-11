# CeDesk: Frontend update (what's new since this morning's guide)

_For the frontend team. 11 Nov 2026. From Ritesh._

This morning you got **CEDESK_FRONTEND_GUIDE.md**. Since then the rest of Phase 1 (and the first part of Phase 2) is built, merged and **live on the test server**. This file lists only what is new or changed. The full, updated guide is attached too (same sections, numbers below point to it).

- API base: `https://cedesk.156-67-82-131.sslip.io/api/v1` (unchanged)
- Every route, with request and response shapes: https://cedesk.156-67-82-131.sslip.io/docs
- Test logins: unchanged

---

## 1. Summary

| # | What's new | Guide section | Build it now? |
|---|---|---|---|
| 1 | Forgot password and reset password pages | 5.1 | ✅ Yes |
| 2 | SLA timer on every ticket, sort by "time left", Settings › SLA | 5.3, 5.14 | ✅ Yes |
| 3 | Files on replies and notes, virus-scan states | 5.6 | ✅ Yes |
| 4 | Saved replies (`/` in the reply box) | 5.10 | ✅ Yes |
| 5 | Rules (automation) and customer-reply reopen | 5.11 | ✅ Yes |
| 6 | Macros | 5.12 | ✅ Yes |
| 7 | Email: support addresses, send test, delivery status, email tickets arrive by themselves | 5.13 | ✅ Yes |
| 8 | Failed emails / failed events list with "Send again" | 3.7 | ✅ Yes |
| 9 | `Idempotency-Key` on create actions (no double replies) | 3.6 | ✅ Yes |
| 10 | Cevanos chats: Settings › Cevanos, **Accept** button, AI messages | 5.5, 5.15 | ✅ Yes (screens). Live chats need Cevanos's side, see section 4 |
| 11 | New message kinds: `customer`, `agent`, `ai`, `note`, `system` | 5.5 | ✅ Yes, **changed** |
| 12 | New error: 503 `Unavailable` | 3.2 | ✅ Yes |

---

## 2. Changes to things you may have already built

| Where | Before (this morning) | Now |
|---|---|---|
| Message `kind` | `reply`, `note`, `system` | `customer`, `agent`, `ai`, `note`, `system`. You still **send** `kind: "reply"` or `"note"`; a sent reply comes back as `agent`. `ai` = Cevanos's AI before the handover: show with an "AI" label |
| Ticket list `sort` | `priority \| newest \| oldest \| updated` | Also `sla` ("time left": soonest due or most overdue first, resolved last) |
| Ticket row / ticket | — | New field `sla` (see 5.14) and `accepted` (see 5.15) |
| Every message | — | New field `attachments: [{id, name, size, mime, scan_status}]` (empty list when none), and `delivery` on email replies |
| Reply on a handover ticket | Allowed | **409** until the agent clicks **Accept**. Notes are still allowed |
| Reopen | Only by setting status back to open | A customer reply also reopens a resolved ticket (not a plain "thanks") |
| Section 6 "Not ready yet" | Accept, SLA, files on replies, forgot password, replies reaching customers | All of these are **ready now** |

---

## 3. What's new, in short

### 3.1 Forgot password (guide 5.1)
- "Forgot password?" link → `POST /auth/forgot-password` `{workspace, email}` → always **202**. Show: "If that account exists, we've emailed a link. It works for 30 minutes."
- Page `/reset-password`: read the token after `#token=` in the address, ask for the new password twice → `POST /auth/reset-password` `{token, new_password}`.
  - 204 → "Password changed, sign in"
  - 410 → "This link is used or expired, ask for a new one"
  - 422 → password rule (10–128 characters)
- Clear the token from the address bar after reading it; keep it out of logs and analytics.

### 3.2 Safe retries: `Idempotency-Key` (guide 3.6)
- On **New ticket**, **Send reply / note**, **file upload**, **Run macro** and **Accept**: send header `Idempotency-Key: <new UUID>`, made once per click, and the **same** value when you retry.
- A repeat returns the first result (`Idempotent-Replayed: true`) instead of doing it twice. Same key with a different request → 422.

### 3.3 Failed emails and events (guide 3.7, Admin)
- `GET /email/failed` → list; **Send again** (when `can_retry`) → `POST /email/{id}/retry` → 204.
- `GET /outbox/failed`, `POST /outbox/{id}/retry`: same pattern.

### 3.4 SLA timer (guide 5.14)
- Pill from `sla.state`: `on_track` grey/green, `close` amber, `breached` red "Overdue …", `met` tick or hidden.
- Time: `sla[sla.current].minutes_left` (negative = overdue). `sla.paused` → "Paused" badge (outside business hours or holiday).
- Settings › SLA: `GET /sla` (everyone), `PUT /sla/targets` (Admin). Policies, hours and holidays are read-only for now.

### 3.5 Files on replies and notes (guide 5.6)
- 📎 in the reply/note box: upload each file first (as for ticket files), then send `POST /tickets/{id}/messages` `{kind, body, attachment_ids: [...]}` (max 10; `body` may be empty with files).
- `scan_status`: `not_scanned` / `clean` → download; `GET /attachments/{id}` **409** = "Checking…" (try again); `infected` / `scan_failed` → red "Blocked", no download.
- Email tickets: reply files max 18 MB together.

### 3.6 Saved replies (guide 5.10)
- Type `/` in the reply box → `GET /saved-replies?q=<text after />` → insert `body`. Placeholders like `{{customer.first_name}}` are filled by the server on send.
- Admin: `POST / PATCH / DELETE /saved-replies`.

### 3.7 Rules (guide 5.11, Admin)
- `GET /rules`, `POST /rules`, `PATCH /rules/{id}`, `DELETE /rules/{id}`, reorder with `PUT /rules/order` `{"ids": [...]}`.
- Events: "When a ticket is created" / "When the customer replies". Show `problems` as a warning.
- Rules act by themselves; the ticket shows grey "Rule applied: …" lines.

### 3.8 Macros (guide 5.12)
- Macros menu on the ticket: `GET /macros` → click → `POST /tickets/{id}/macros/{macro_id}` with `If-Match`.
- Admin: `POST / PATCH / DELETE /macros`, reorder `PUT /macros/order`. Hide the menu for light agents.

### 3.9 Email (guide 5.13)
- Settings › Email (Admin): `GET / POST / PATCH / DELETE /email-addresses`, **Send test** → `POST /email-addresses/{id}/test` → 202.
- Under each agent reply on an email ticket: `delivery` → `pending` "Sending…", `sent` "Emailed", `failed` "Not delivered" (red).
- Customer emails now become tickets by themselves (channel `email`), and their answers are added to the same ticket.

### 3.10 Cevanos chats and Accept (guide 5.15)
- **Settings › Cevanos (Admin):** `GET / PUT / DELETE /cevanos/connection`, "Test the key" → `POST /cevanos/connection/check`. Secrets are never sent back: show empty password fields and `api_key_hint`. Show the webhook address (`<API address>` + `webhook_path`) for the Admin to paste into Cevanos.
- **Accept** button when `accepted` is `false` → `POST /tickets/{id}/accept` (with `Idempotency-Key`). Before Accept: disable **Reply**, keep **Note**.
- Chat tickets: `channel: "chat"`, `source: "cevanos"`. Handover box: `handover.reason`, `handover.channel_kind`, `handover.order_id`, plus `sentiment`.
- No files on chat replies (422): offer a note instead.
- "Make a ticket from a Cevanos chat" → `POST /tickets/from-conversation/{conversation_id}`.

### 3.11 New error code (guide 3.2)
| Status | `code` | Show |
|---|---|---|
| 503 | `Unavailable` | `message` (e.g. "Cevanos can't be reached now") with a **Retry** button |

---

## 4. Still not live (build the screen, it will start working by itself)

| What | Waiting for |
|---|---|
| Chat replies reaching the customer, chat history on the ticket, customer chat messages arriving | Cevanos team adding their `/v1` routes. Until then such sends show up in Settings › Failed events |
| Real customer name/email on chat tickets (now "Chat customer") | Cevanos team |
| Real emails arriving in inboxes on the test server | A mail account on the server. Until then `delivery` shows `sent` but nothing reaches an inbox |

## 5. Coming in Phase 2 (don't build yet)
Customer portal, CSAT, SLA policy / business hours / holiday editors, SLA breach alerts, reports.

---

Questions: ask Ritesh. If a route behaves differently from this file, the `/docs` page is the source of truth.

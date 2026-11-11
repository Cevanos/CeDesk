# CDesk: Frontend Guide (Phase 1)

_For the frontend team. Last updated 10 Nov 2026. Backend owners: Ritesh (tickets, people, routing), Pranav (SLA, files, Cevanos connector, email)._

---

## 1. Where things are

| What | Link |
|---|---|
| API base URL | `https://cedesk.156-67-82-131.sslip.io/api/v1` |
| Try every route in the browser | https://cedesk.156-67-82-131.sslip.io/docs |
| OpenAPI file (for code generation) | `docs/api/openapi.json` in the CDESK repo |
| What changed in the API | `docs/api/CHANGELOG.md` in the CDESK repo |
| How every screen should look and behave | The CDesk mock: https://claude.ai/artifact/63T5fWbbjtgFdjwk6EyK66 |

**Your local frontend must run on `http://localhost:3000` or `http://localhost:5173`.** The API only accepts browser calls from these two addresses. If you need another one, tell Ritesh.

## 2. Test logins

Workspace: **`acme-store`**. Everyone uses **the same password, which Ritesh sends you privately.**

| Role | Name | Email |
|---|---|---|
| Admin | Ritesh | `ritesh@acme-store.in` |
| Supervisor | Adarsh Dubey (on break) | `adarsh@acme-store.in` |
| Agent | Neha | `neha@acme-store.in` |
| Agent | Chetan | `chetan@acme-store.in` |
| Agent | Pranav | `pranav@acme-store.in` |
| Agent | Anurag (offline) | `anurag@acme-store.in` |
| Light agent | Meena | `meena@acme-store.in` |

The test data has 3 teams (Billing, Shipping, Tech), 5 ticket types and 6 tickets, including an AI handover, a VIP Hindi ticket and a note with an @mention.

**Please don't change the shared password of these accounts.** Use Admin › Add member to make your own test users.

---

## 3. Rules for every call

### 3.1 Sign in and send the token
1. `POST /auth/sign-in` with `{"workspace": "acme-store", "email": "...", "password": "..."}`
2. You get `{token, expires_at, member_id, role}`.
3. Send `Authorization: Bearer <token>` on every other call.
4. On **401** anywhere: the session ended. Go back to the sign-in screen.
5. Sign out: `POST /auth/sign-out` (returns 204).

Store the token in memory or `sessionStorage`. There are no cookies.

### 3.2 Errors always look the same
```json
{"error": {"code": "Invalid", "message": "Some fields are not valid.",
           "details": [{"field": "subject", "location": "body", "message": "..."}]}}
```

| Status | `code` | Show the user |
|---|---|---|
| 401 | `NotSignedIn` | Sign-in screen |
| 403 | `Forbidden` (or a specific code) | `message` ("You can't do this") |
| 404 | `NotFound` | "Not found" |
| 409 | `Conflict` | "Someone else changed this. Reload." |
| 422 | `Invalid` | Put each `details[].message` under its `field` |
| 429 | `TooManyAttempts` | "Too many tries. Wait N seconds" (N = `Retry-After` header) |
| 500 | `Internal` | "Something went wrong" + the `X-Request-ID` header for us |

**Always show `error.message`.** It's written for the end user.

### 3.3 Two people editing the same ticket (`ETag` / `If-Match`)
- `GET /tickets/{id}` returns an `ETag` header.
- Send it back as `If-Match` on `PATCH /tickets/{id}` and `POST /tickets/{id}/resolve`.
- If someone changed the ticket in between, you get **409**. Reload the ticket and show the new data.
- Every successful change returns a new `ETag`. Keep the latest one.

### 3.4 Lists load page by page
- Every list returns `next_cursor` (members: `next_after`).
- To get the next page, send it back as `?cursor=...` (members: `?after=...`).
- `null` means there are no more pages.
- `limit` is 1–200 (default 50).

### 3.5 Other
- All times are UTC ISO strings (`2026-11-10T09:13:00Z`). Show them in the user's local time.
- IDs are UUID strings. Show tickets by `label` (`T-1005`), never by `id`.
- `X-Request-ID` is on every response. Show it in error popups so we can find the log line.

---

## 4. What each role can do

Hide or disable buttons the user can't use. The API will refuse them anyway (403).

| Action | Light agent | Agent | Supervisor | Admin |
|---|---|---|---|---|
| See tickets | ✅ | ✅ | ✅ | ✅ |
| Private note (+ @mention) | ✅ | ✅ | ✅ | ✅ |
| Reply to the customer, resolve | ❌ | ✅ | ✅ | ✅ |
| Create a ticket, change fields/status/priority/tags | ❌ | ✅ | ✅ | ✅ |
| Assign to **myself** | ❌ | ✅ | ✅ | ✅ |
| Assign to **someone else** | ❌ | ❌ | ✅ | ✅ |
| Change **own** status (available, break…) | ❌ (light agents have no status) | ✅ | ✅ | ✅ |
| Settings: members, teams, ticket types, routing, reset passwords | ❌ | ❌ | ❌ | ✅ |

Get the signed-in user and their role from `GET /me`.

---

## 5. Screens and the APIs behind them (ready now ✅)

### 5.1 Sign-in
| UI | API |
|---|---|
| Workspace + email + password | `POST /auth/sign-in` |
| Wrong password, unknown email, inactive or locked account | All give the same 401 on purpose. Show "Wrong workspace, email or password". After 10 wrong tries the account is locked for 15 minutes (an Admin can unlock it with Reset password) |
| 20 tries in 5 minutes from one address | 429; show the wait time from `Retry-After` |

### 5.2 Top bar / avatar menu
| UI | API |
|---|---|
| My name, role, workspace name | `GET /me` |
| My status (Available / On break / Transfers only / Offline) | `PUT /me/status` `{"status": "on_break"}` |
| **Change password** | `POST /me/password` `{"current_password", "new_password"}` → 204. New password 10–128 characters. The user stays signed in here; other devices are signed out. |
| Bell: unread count + list | `GET /notifications` (`?unread=true` for unread only). `unread` = badge number |
| Click a notification | `POST /notifications/{id}/read`, then open `ticket_id` |
| "Mark all read" | `POST /notifications/read-all` |
| Sign out | `POST /auth/sign-out` |

Notification `kind`: `assigned` (a ticket was given to you) or `mention` (someone @mentioned you in a note). Show `text` as is. There's no live push yet: **refresh the bell every 30 seconds.**

### 5.3 Ticket list
`GET /tickets` with these query options:

| UI | Query |
|---|---|
| Tabs Open / Pending / Resolved + their counts | `status=open\|pending\|resolved`; counts are in `counts` of every answer |
| "Mine" / "Unassigned" / "Everyone" | `scope=mine\|unassigned\|everyone` |
| Priority filter | `priority=urgent\|high\|medium\|low` |
| Team filter | `team=<team id>` |
| Search box | `q=` (ticket number like `T-1005` or `1005`, subject, customer name/email, any field value, tag) |
| Sort | `sort=priority` (default) `\|newest\|oldest\|updated` |
| Load more | `cursor=<next_cursor>` |

Each row has: `label`, `subject`, `status`, `priority`, `channel`, `type_name`, `team_name`, `assignee_name`, `contact {name, email, vip}`, `tags`, `created_at`, `updated_at`, plus AI fields `topic`, `sentiment`, `language`, `source` (these may be `null`).

### 5.4 New ticket
`POST /tickets`:
```json
{"subject": "Order not delivered", "contact": {"name": "Asha", "email": "asha@example.com"},
 "description": "first message (optional)", "priority": "high", "channel": "web",
 "type_id": "...", "team_id": "...", "assignee_id": null,
 "fields": {"order_id": "A-123"}, "tags": ["delivery"]}
```
- Only `subject` and `contact` are required.
- If you choose a team but no assignee, the ticket is **auto-assigned** (round-robin). The reason appears as a system line in the ticket.
- `fields` must match the chosen ticket type's fields (see 5.7).

### 5.5 Ticket page
| UI | API |
|---|---|
| Open the ticket | `GET /tickets/{id}`. Includes the latest `messages` and `older_messages_cursor` |
| "Load older messages" | `GET /tickets/{id}/messages?cursor=<older_messages_cursor>` |
| Change status / priority / type / team / assignee / subject / fields | `PATCH /tickets/{id}` with only what changed, plus `If-Match` |
| Add or remove tags | `PATCH` with `{"add_tags": [...], "remove_tags": [...]}` (max 20 tags) |
| Resolve button | `POST /tickets/{id}/resolve` with `If-Match` |
| Reply to the customer | `POST /tickets/{id}/messages` `{"kind": "reply", "body": "..."}` |
| Private note with @mentions | `{"kind": "note", "body": "...", "mention_ids": ["<member id>"]}`. Mentioned people get a notification |
| Customer card (right side) | `contact` in the ticket: `name`, `email`, `vip`, `customer_since`, `other_tickets` |
| AI handover box (top of a ticket from the AI) | `handover` object + `topic`, `sentiment`, `language`. Show it when `handover` is not `null` |
| Files | `GET /tickets/{id}/attachments`; upload and download, see 5.6 |

Messages: `kind` is `reply`, `note` or `system` (you only send `reply` or `note`). Show **notes** in yellow, **system lines** small and grey (for example "Assigned to Neha: fewest open tickets"), **customer messages** where `author_member_id` is `null`.

Change rules the UI should follow:
- Changing the **team** re-assigns the ticket automatically unless you also send `assignee_id`.
- Setting a resolved ticket back to open counts as a reopen (`reopen_count` goes up). Reopening by itself when the customer replies comes in Phase 2.
- Status values: `open`, `pending`, `resolved`.

### 5.6 Files on a ticket (Pranav)
| UI | API |
|---|---|
| Upload | `POST /tickets/{id}/attachments?name=<file name>` with the **raw file as the body** (not a form) and the right `Content-Type`. Max **25 MB** → 413 if larger |
| List | `GET /tickets/{id}/attachments` |
| Download | `GET /attachments/{id}` → `{url, expires_at}`. Open `url` straight away; it expires after a short time |

### 5.7 Customer page
| UI | API |
|---|---|
| Customer card + all their tickets | `GET /contacts/{contact_id}/tickets` |
| Edit name, mark VIP | `PATCH /contacts/{contact_id}/profile` `{"name": "...", "vip": true}` |

### 5.8 Agents / workload screen
| UI | API |
|---|---|
| Everyone's status, open tickets, channels, capacity, skills | `GET /agents` |
| Admin changes an agent's status, channels, capacity, skills | `PATCH /agents/{member_id}/routing` |
| Workspace default capacity per channel | `GET` / `PATCH /routing-defaults` (`cap_email`, `cap_chat`, `cap_messaging`) |

Agent status values: `available`, `on_break`, `transfers_only`, `offline`. Channel groups: `email`, `chat`, `messaging`. **New members start Offline**; they must set themselves Available to get tickets.

### 5.9 Settings (Admin only)
| Screen | API |
|---|---|
| **Members**: list | `GET /members` (`?after=` for more) |
| Add member | `POST /members` `{"email", "name", "role", "password"}` |
| Change name / role / deactivate | `PATCH /members/{id}` `{"name"?, "role"?, "is_active"?}`. The last active Admin can't be removed |
| **Reset password** (+ "Locked" label in the mock) | `POST /members/{id}/password` `{"new_password"}` → 204. Signs that member out everywhere and unlocks them. Not allowed on yourself (use Change password) |
| **Teams**: list / add / edit / delete | `GET`, `POST /teams`, `PATCH /teams/{id}`, `DELETE /teams/{id}`. Fields: `name`, `auto_assign`, `max_open_per_agent` (1–50), `member_ids` |
| **Ticket types**: list / add / edit / delete / reorder | `GET`, `POST /ticket-types`, `PATCH /ticket-types/{id}`, `DELETE /ticket-types/{id}`, `PUT /ticket-types/order` `{"ids": [...]}` |

Ticket type fields: `{key, label, kind: "text"|"number"|"select", options: [...], required}`, max 30 per type. `required_skill` on a type means only agents with that skill get it automatically.

---

## 6. Not ready yet: build the screen, show "Coming soon"

| Mock feature | Waiting on | Owner |
|---|---|---|
| **Accept** button on an AI chat handover | Cevanos connector | Pranav |
| Replies actually reaching WhatsApp / chat / email customers | Connector + email sending | Pranav |
| Customer replies coming in by themselves | Connector + incoming email | Pranav |
| **SLA timer** on tickets and "sort by time left" | SLA wired to tickets | Pranav, then Ritesh |
| Files attached **to a reply or note** (files on the ticket already work) | Attachments on messages | Pranav |
| "Forgot password?" link on sign-in | Email sending | Pranav, then Ritesh |
| Live updates without refreshing | Not planned in Phase 1 | Poll every 30 s |

Today, a reply you send is saved and shown in the ticket, but isn't delivered to the customer yet.

## 7. Coming in Phase 2 (don't build yet)
Rules (automation), macros, saved replies, thank-you detection, customer portal, SLA settings screens. The API for these will be added to `CHANGELOG.md` as it's built.

---

## 8. Quick test with curl
```bash
API=https://cedesk.156-67-82-131.sslip.io/api/v1
TOKEN=$(curl -s $API/auth/sign-in -H 'Content-Type: application/json' \
  -d '{"workspace":"acme-store","email":"ritesh@acme-store.in","password":"<the password>"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s $API/me -H "Authorization: Bearer $TOKEN"
curl -s "$API/tickets?status=open" -H "Authorization: Bearer $TOKEN"
```

## 9. Questions or problems
Send Ritesh the **route, the status code, the `error.message` and the `X-Request-ID`**. The test server is updated by hand after each merge, and every API change is listed in `docs/api/CHANGELOG.md`.

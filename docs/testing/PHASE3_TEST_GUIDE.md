# CeDesk Phase 3: test guide for the tester

How to test the Phase 3 features by hand: the demo data, the mobile app routes, phone
registration for push, the Calendly app, Slack side conversations, the archive and
self-hosting. Each test says what to do and what you should see. Mark each one **Pass**
or **Fail**.

Every feature here already has automated tests. This guide checks the same behaviour on a
real server, the way a user would hit it.

| Section | Feature | Plan IDs | Needs anything special? |
|---|---|---|---|
| 1 | Demo and sample tickets | D1–D3 | `CEDESK_DEMO_ENABLED=true` on the server |
| 2 | Mobile app: queue and one-tap reply | P8 | Section 1 done first |
| 3 | Phones for push banners | P8 | Part B: a Firebase test project and a phone |
| 4 | Calendly app and the `/book` saved reply | PL7, W19 | Nothing |
| 5 | Slack side conversations | X5 | Part B: a test Slack workspace and bot |
| 6 | Archive of old resolved tickets | O3 | A developer to make tickets "old" (6.0) |
| 7 | Self-hosting | Docs | A clean machine with Docker |

---

## Before you start

**Where to test.** Use the **test server**, never production. Every route below is under
`/api/v1` (left out here for short). The easiest way to call them is the API page at
`<server>/docs`:

1. Open `<server>/docs`.
2. Call `POST /api/v1/auth/sign-in` with `{"workspace": "<workspace>", "email": "...",
   "password": "..."}`.
3. Copy the `token` from the answer, click **Authorize** at the top, and paste it.
4. Every call now runs as that person. To act as someone else, sign them in the same way
   and paste their token instead.

**Accounts.** Make these in one test workspace before you start. The Admin adds members
with `POST /members` (`email`, `name`, `role`, `password`).

| Who | Role | Used in |
|---|---|---|
| Admin | `admin` | Everything |
| Neha | `agent` | Mobile, push, archive |
| Arjun | `agent` | Push (a second person) |
| Lata | `light_agent` | Mobile (a free seat that can't reply) |
| Sam | `custom`, a role with scope `team_only` | Mobile, archive (team scope) |

To make Sam's role: create two teams with `POST /teams` (for example **Billing** and
**Shipping**). Then `POST /roles` with
`{"name": "Shipping only", "scope": "team_only", "team_id": "<Shipping's id>"}`, and add
Sam with `"role": "custom", "role_id": "<that role's id>"`.

**Reporting.**

- For every **Fail**, write what you did, what you expected and what you got, and attach
  a screenshot of the answer.
- **Never paste passwords, tokens, API keys, Slack tokens or Firebase keys** into chat,
  tickets or this file. If a screenshot shows one, blur it.
- When an answer is an error, note its status code (403, 404, 409, 422...) and its
  message.

---

## 1. Demo and sample tickets (D1–D3)

For sales demos and for trying CeDesk out. Admin only. Only on a server that has
`CEDESK_DEMO_ENABLED=true` and is not production.

| ID | Do this | You should see | Result |
|---|---|---|---|
| DEMO-1 | As **Admin**: `POST /demo/seed` | 201, `{"created": 16, "deleted": 0}` | |
| DEMO-2 | `GET /tickets` | The demo tickets: 4 live ones assigned to you (one is Pending), 1 chat with `accepted: false` (waiting), 6 new samples from email, chat and the web. The 5 past ones are under Resolved. | |
| DEMO-3 | Open a resolved demo ticket (`GET /tickets/{id}`) | It has a CSAT rating, and a first reply and resolution that took hours | |
| DEMO-4 | Open the "refund" sample | It went to the **Billing** team at **High** priority (the default rules ran on it) | |
| DEMO-5 | `POST /demo/seed` again | 409 "Demo data is already there; use reset." | |
| DEMO-6 | Make one ticket by hand (`POST /tickets`). Then `POST /demo/reset` | 200 with `created` and `deleted` counts. The demo tickets are new. Your hand-made ticket is still there. | |
| DEMO-7 | `POST /demo/samples`. Then, in a second workspace with no demo data, call it again. | First 409 (the seed already made the samples). In the empty workspace, 201 with `created: 6`. | |
| DEMO-8 | As **Neha** (agent): `POST /demo/seed` | 403 | |
| DEMO-9 | Reply to a demo ticket as an agent | The reply is saved but **no email goes out**. Demo customers have `.invalid` addresses. | |
| DEMO-10 | `GET /audit` (Admin) | Entries in area `demo` for the seed and the reset | |
| DEMO-11 | *(Only if you have a server without the setting)* `POST /demo/seed` | 403 "Demo data is off on this server..." | |

---

## 2. Mobile app: queue and one-tap reply (P8)

Two routes made for the phone. Do section 1 first so there are tickets to work with.

| ID | Do this | You should see | Result |
|---|---|---|---|
| MOB-1 | As **Admin**: `GET /me/queue` | Tickets that are yours or nobody's, not resolved. **Yours first**, then chats waiting to be accepted, then by SLA time left. Each item has `mine`, `waiting_to_accept`, the customer's name and the last message (at most 140 characters). | |
| MOB-2 | Look at a ticket in the queue that has an internal note as its newest message | The queue shows the last message the **customer** could see, never the note | |
| MOB-3 | `GET /me/queue?limit=0` and `?limit=101` | 422 for both (1 to 100 allowed) | |
| MOB-4 | Assign a ticket to Neha, then resolve another one. Call `GET /me/queue` as Admin. | Neha's ticket and the resolved one are **not** in Admin's queue | |
| MOB-5 | Find the waiting chat (`waiting_to_accept: true`). `POST /tickets/{id}/mobile-reply` with `{"body": "Hi, I'm here to help"}`. Add the header `Idempotency-Key: mob-test-1` if you can. | 201 with `accepted: true` and `saved_as: "reply"`. On the ticket: a line "<your name> joined the chat (mobile)", then your reply. | |
| MOB-6 | Send exactly the same request again (same `Idempotency-Key`) | The same answer, and **only one** reply on the ticket | |
| MOB-7 | Find a ticket with the chat still waiting (reset the demo if needed). As **Lata** (light agent): `POST /tickets/{id}/mobile-reply` | 201 with `saved_as: "note"`. It is a private note, and the chat is **still waiting**. | |
| MOB-8 | As **Sam** (Shipping only): `GET /me/queue` | Only Shipping tickets | |
| MOB-9 | `POST /tickets/<random uuid>/mobile-reply` | 404 | |
| MOB-10 | Make a role with scope `team_edit` for Shipping. As a member with it, `mobile-reply` on a Billing ticket. | 403 (they can view it, not reply) | |

---

## 3. Phones for push banners (P8)

**Part A works on any server.** **Part B** needs the two Firebase settings on the server
(`CEDESK_PUSH_FCM_PROJECT_ID`, `CEDESK_PUSH_FCM_SERVICE_ACCOUNT`) and a phone with the app.

Test tokens in Part A are made up. Use 20 or more characters with no spaces, and a
different ending for each one, e.g. `test-token-neha-0001`.

### Part A: registering phones (no Firebase needed)

| ID | Do this | You should see | Result |
|---|---|---|---|
| PUSH-1 | As **Neha**: `GET /me/devices` | `push_on: false` when Firebase isn't set (true when it is), `devices: []` | |
| PUSH-2 | `POST /me/devices` with `{"platform": "android", "token": "test-token-neha-0001"}` | 201 | |
| PUSH-3 | `GET /me/devices` | One device with `token_hint: "a-0001"` (the last 6 characters). The full token is **never** shown. | |
| PUSH-4 | Send the same token again | 201, and still **one** device | |
| PUSH-5 | Try a token that is too short (`abc`), a token with a space, and `"platform": "windows"` | 422 for each | |
| PUSH-6 | As **Arjun** (Neha still signed in): register `test-token-neha-0001` | 409 (it belongs to someone still signed in on it) | |
| PUSH-7 | Neha signs out (`POST /auth/sign-out` with her token). Arjun tries again. | 201: the phone moves to Arjun. When Neha signs in again, her `GET /me/devices` is empty. | |
| PUSH-8 | As Neha: `DELETE /me/devices/{Arjun's device id}` | 404 | |
| PUSH-9 | As Arjun: `DELETE /me/devices/{his device id}` | 204, and it's gone from his list | |
| PUSH-10 | Register 11 different tokens as Neha | Only the **10 newest** stay | |
| PUSH-11 | Admin gives Neha a new password (`POST /members/{id}/password`) | Neha's phones are gone (that ended her sign-ins) | |
| PUSH-12 | Admin makes an API key (`POST /api-keys`, access `read_write`). Call `POST /me/devices` with the key as the token. | 403 | |

### Part B: real banners (needs Firebase and a phone)

| ID | Do this | You should see | Result |
|---|---|---|---|
| PUSH-B1 | Sign in on the phone app as Neha. Assign her a ticket from the web. | Banner "New ticket for you", body is the ticket number only (e.g. `T-1042`), **never** the subject | |
| PUSH-B2 | @mention Neha in a note | Banner "You were mentioned" | |
| PUSH-B3 | The customer replies on Neha's ticket | Banner "The customer replied" | |
| PUSH-B4 | A Cevanos chat waits for Accept on Neha's team | Banner "A chat is waiting for you". No banner once it's accepted. | |
| PUSH-B5 | Tap a banner | The app opens that ticket | |
| PUSH-B6 | Sign out on the phone, then assign Neha a ticket | No banner on that phone | |

---

## 4. Calendly app and the `/book` saved reply (PL7, W19)

As **Admin**. Calendly needs no secret: a booking link is public.

| ID | Do this | You should see | Result |
|---|---|---|---|
| CAL-1 | `GET /apps` | `calendly` is listed with `settings_fields: ["link"]` and `credential: null` | |
| CAL-2 | `PUT /apps/calendly` with `{"settings": {"link": "https://calendly.com/acme/30min"}}` | Installed | |
| CAL-3 | `GET /saved-replies?q=book` | A reply with `shortcut: "book"`, `source: "calendly"`. Its text is "Hi {{customer.first_name}}, you can pick a time that suits you here: https://calendly.com/acme/30min". | |
| CAL-4 | Try these links: `http://calendly.com/acme` (not https), `https://evil.example.com/acme`, `https://calendly.com.evil.com/x`. Also send a `"secret"`. | 422 for each | |
| CAL-4b | Paste the link as the browser's address bar shows it, e.g. `www.calendly.com/acme/30min?month=2026-11` (no https://, with www. and ?month=) | Saved as `https://calendly.com/acme/30min`; `/book` has that, nothing after `?` | |
| CAL-5 | Change the link to `https://calendly.com/acme/60min` | `/book` now has the 60min link; the rest of its text is unchanged | |
| CAL-6 | Edit `/book` by hand so the link is gone (`PATCH /saved-replies/{id}`). Then change the Calendly link again. | The answer's `note` says "/book was edited by hand; update its link yourself." Your text is not touched. | |
| CAL-7 | `DELETE /apps/calendly` with the edited reply | The reply is **kept**, now with `source: "manual"` | |
| CAL-8 | Delete the kept `/book` (`DELETE /saved-replies/{id}`). Install Calendly again, then, without editing `/book`, `DELETE /apps/calendly`. | `/book` is deleted with the app | |
| CAL-9 | Make your own saved reply `/book` first, then install Calendly | Calendly's reply is `/book1`; yours is untouched. Removing Calendly never deletes yours. | |
| CAL-10 | `GET /audit` | Entries in area `apps` and `saved_replies` for the installs, changes and removals | |

---

## 5. Slack side conversations (X5)

A side conversation in a Slack channel instead of by email. The customer never sees it.

### Part A: without a Slack workspace

| ID | Do this | You should see | Result |
|---|---|---|---|
| SLK-1 | `GET /apps` | `slack` ("Slack side conversations") with `card: "side"` and `second_credential: "signing_secret"` | |
| SLK-2 | Without the app installed: `POST /tickets/{id}/side-conversations/slack` with `{"channel": "#warehouse", "body": "Is order 1234 packed?"}` | 409 (the app isn't installed) | |
| SLK-3 | `PUT /apps/slack` with a made-up bot token and signing secret | 422 (Slack refused the token); nothing is installed. A 503 means the server couldn't reach Slack: note it. | |
| SLK-4 | `PUT /apps/jira` (or any other app) with a `signing_secret` | 422 (only Slack takes a `signing_secret`) | |
| SLK-5 | `POST /slack/events/{workspace_id}` with any body and no Slack signature | 401, nothing stored | |

### Part B: with a test Slack workspace and bot

The Slack app needs:
- the bot scopes `chat:write`, `channels:history`, `groups:history`, `users:read` and `files:read` (for a shared file's name);
- the events `message.channels` and `message.groups`;
- the Request URL `<server>/api/v1/slack/events/<workspace_id>`.

| ID | Do this | You should see | Result |
|---|---|---|---|
| SLK-B1 | `PUT /apps/slack` with the real bot token and signing secret | Installed. Only the token's last 4 characters are shown. | |
| SLK-B2 | Invite the bot to `#warehouse`. Start a side conversation there from a ticket. | 201. The bot posts your message with the ticket number and subject, **never** the customer's messages. | |
| SLK-B3 | Reply in that Slack thread as a person | The reply shows on the side conversation, with the person's name. The ticket gets a line like "Ravi replied in the side conversation in Slack #warehouse", and the assignee gets a notification. | |
| SLK-B4 | Reply from CeDesk (`POST /side-conversations/{id}/messages`) | It appears in the same Slack thread | |
| SLK-B5 | Reply in the thread with a photo, mentioning a colleague (`@name`) and pasting a link | Your text with `@<their name>` (not `@U…`) and the link once, plus a line `[file: <name>]` | |
| SLK-B6 | Edit or delete a Slack reply, or post outside the thread | Nothing changes on the ticket | |
| SLK-B7 | Start one in a channel the bot isn't in, and in one that doesn't exist | 422 | |
| SLK-B8 | Uninstall the app, then reply in the thread | Nothing is stored | |

---

## 6. Archive of old resolved tickets (O3)

Resolved tickets older than the archive age (default 120 days) leave the ticket list once
a day. They can still be found and opened, read only.

> Tests ARC-5 and ARC-6 need PR #52 (the archived ticket page) merged on the test server.

### 6.0 Setup (a developer does this)

Tickets can't be made old through the API, so a developer does it on the test server:

1. Make the test tickets (see the tests below). Resolve them in CeDesk, and note their ids.
2. As the database superuser, make them look resolved 200 days ago:
   ```sql
   UPDATE hd_ticket SET resolved_at = now() - interval '200 days'
   WHERE id IN ('<ticket id>', '<ticket id>') AND status = 'resolved';
   ```
3. Restart the worker (archiving runs when the worker starts, then once a day), or run
   `cedesk worker --once`.

Prepare these tickets for the tests, then archive them in one go:

- **Ticket A** (Billing team, assigned to Neha):
  - a reply and an internal note;
  - a file;
  - a CSAT rating;
  - a ticket type;
  - time logged, a task and an approval;
  - a link to another **resolved** ticket;
  - a side conversation by email.
- **Ticket B**: resolved and backdated, but linked to an **open** ticket.
- **Ticket C**: resolved and backdated, with a child ticket that is still open.

### 6.1 Tests

| ID | Do this | You should see | Result |
|---|---|---|---|
| ARC-1 | As **Admin**: `GET /archive/settings` | `after_days: null`, `effective_days: 120` | |
| ARC-2 | `PUT /archive/settings` with `{"after_days": 29}`, then `3651`, then `30`, then `null` | 422, 422, OK (30), OK (back to 120). As Neha (agent): 403. The change is in `GET /audit` (area `archive`). | |
| ARC-3 | After the worker ran: `GET /tickets` and `GET /tickets/{Ticket A id}` | Ticket A isn't in the list, and opening it live is 404 | |
| ARC-4 | `GET /archive/tickets?number=<A's number>`, then `?number=T-<number>`, `?email=<customer email>` and `?contact_id=<id>` | Ticket A is found every way. With no search at all, or `?number=abc`: 422. | |
| ARC-5 | `GET /archive/tickets/{A's id}` | Its messages (the note too), the files' names and sizes (no download link), the rating, `type_name`, `team_name` and `assignee_name` | |
| ARC-6 | Look at `related` on the same answer | `time_log`, `tasks`, `approvals`, `links` (with the other ticket's `label`) and `side_conversations` (with their messages) are filled in. So is `emails`, if replies were emailed. `cevanos_chat` is null (it wasn't a chat). | |
| ARC-7 | Try to change it: `PATCH /tickets/{A's id}` or reply to it | 404: archived tickets are read only | |
| ARC-8 | As **Sam** (Shipping only): search for Ticket A and open it | Search: `[]`. Open: 404. Switch Sam's role to Billing and it opens. | |
| ARC-9 | Look for Tickets B and C | **Still live**, not archived: B is linked to an open ticket, C has an open child | |
| ARC-10 | Resolve B's open ticket. Resolve C's child and make it old too (6.0, step 2). Run the worker, check, then run it once more. | First run: B and C's child move to the archive, C stays. Second run: C moves too (a parent waits until its children are archived). | |
| ARC-11 | `POST /privacy/export` with `{"email": "<A's customer>"}`, then download the file (`GET /privacy/requests/{id}/file`) | Ticket A is in the file with `"archived": true` | |
| ARC-12 | `GET /usage` | `storage_bytes` still counts Ticket A's file | |
| ARC-13 | *(Only if the test server receives email)* Ticket A was made by email from a mailbox you control. Reply to CeDesk's old email from that mailbox. | A **new** ticket, tagged `archived-follow-up`, with the line "Follows archived ticket T-<number>" | |

---

## 7. Self-hosting (docs)

Check that someone can run CeDesk on their own server from the docs alone.

| ID | Do this | You should see | Result |
|---|---|---|---|
| SH-1 | On a clean machine with Docker, follow `docs/SELF_HOSTING.md` from the start. Don't use any other help. | It runs. Note any step that was unclear or wrong. | |
| SH-2 | `curl <your server>/readyz` | `{"status": "ok", "edition": "self_hosted"}` | |
| SH-3 | Make the first workspace as the doc says, then sign in | Sign-in works | |
| SH-4 | Pick three settings in `deploy/env.example` | Each one is explained in `docs/SETTINGS.md` | |

---

## Results

| Section | Tests | Pass | Fail | Not run (why) |
|---|---|---|---|---|
| 1. Demo | 11 | | | |
| 2. Mobile | 10 | | | |
| 3. Push, Part A | 12 | | | |
| 3. Push, Part B | 6 | | | |
| 4. Calendly | 10 | | | |
| 5. Slack, Part A | 5 | | | |
| 5. Slack, Part B | 8 | | | |
| 6. Archive | 13 | | | |
| 7. Self-hosting | 4 | | | |

Tester: ______________________  Date: ______________  Server: ______________

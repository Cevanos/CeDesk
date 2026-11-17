# Import smoke tests on real accounts (P0-7)

The automated tests use recorded or hand-written source answers (mocked HTTP) and a real
PostgreSQL. They cannot prove what a live account answers. This is the procedure for one
smoke test per platform on a **trial or sandbox account**, never a customer's production
account. Nothing here writes to the source: every connector is read-only.

Run it on the test server (or a local CeDesk) against a fresh workspace. Record each result
in the workbook (`CEDESK_IMPORT_MIGRATION_TEST_RESULTS.xlsx`) with the run id.

## 0. Common steps (every platform)

```bash
API=https://<cedesk-host>/api/v1
TOKEN=<an Admin's access token>            # sign in as an Admin first
H=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

# 1. Connect (body per platform below). Expect 201; the response never echoes a secret.
curl -s "${H[@]}" -X POST $API/imports/connections -d @connect.json | tee conn.json
CONN=$(jq -r .id conn.json)

# 2. Discover and dry run. Read every `warnings` line; note the counts.
curl -s "${H[@]}" $API/imports/connections/$CONN/discover | jq .
curl -s "${H[@]}" -X POST $API/imports/connections/$CONN/dry-run -d '{"kinds":["contacts","tickets","articles","replies"]}' | jq .

# 3. Run (add "sync": true for the sync checks in step 6).
curl -s "${H[@]}" -X POST $API/imports/connections/$CONN/run -d '{"kinds":["contacts","tickets","articles","replies"],"sync":true}' | tee run.json
RUN=$(jq -r .id run.json)

# 4. Watch it. `outcome` must be complete or complete_with_exceptions; read `report`.
curl -s "${H[@]}" $API/imports/runs/$RUN | jq '{state, outcome, counts, report}'
curl -s "${H[@]}" $API/imports/runs/$RUN/failures | jq .
```

5. **Check in the UI** (Admin): pick 3 tickets, including the longest and one with files.
   Compare with the source, message by message:
   - message count, order, author, public or private, time (no 1970, no import day);
   - every file opens; any file not copied has a link line, and the report counts it;
   - status, priority, assignee, team, tags (past 20 in the field `more_tags`), custom
     fields (0 and false kept, dropdown labels not ids), `source_url` opens the source;
   - the contact card shows phone and organization when the source has them;
   - the customer portal shows **none** of the imported fields.
6. **Sync** (when the run syncs): in the source, edit one imported ticket (add a reply,
   change status, remove a tag). Within 5 minutes (`POST /imports/runs/$RUN/sync-now` to
   hurry) CeDesk shows the reply once, the new status, the tag removed, and a system line
   "Updated from <source>: ...". Then edit a different ticket **in CeDesk** (status) and
   leave it alone in the source: the next sync must **not** change it back.
7. **Reconcile**: `report.reconciliation[kind].difference` is 0, or every missing record is
   in `/failures` or explained by a `losses` key.
8. **Credentials**: revoke the key in the source, `sync-now`: the run fails with "refused
   these credentials". Make a new key, `POST /imports/connections/$CONN/credentials`
   `{"credentials":{...}}` (expect 200), then `POST /imports/runs/$RUN/resume` (202): the
   run carries on, nothing duplicated. A key for **another** account must give 409.
9. **Stop**: `POST /imports/runs/$RUN/sync-stop`, then confirm the connection is `closed`
   and its credentials gone (`credentials_hint` null).

Pass: steps 4-9 as described, no unexplained difference, nothing duplicated after a second
run of the same import (connect again, run again: every ticket `skipped`).

## Seed data to create in each trial account first

| What | Why |
|---|---|
| 1 ticket with 600+ messages (a script can add notes) | paging past 500 (Intercom), caps |
| 1 ticket whose only message is a file | P0-5 file-only messages |
| 1 ticket with 3 files: PDF, PNG, 30 MB video | copies, the 25 MB limit kept as a link |
| 1 ticket with 25 tags and a custom field set to 0 / unchecked | tags over 20, field 0/false |
| 1 ticket with a CSAT rating, then reopened | CSAT on any status |
| 1 ticket with CC'd people, one merged/linked ticket | recipients, links |
| 1 deleted agent who replied on a ticket | unknown author handling |
| 2 help articles (one with a translation), 2 macros/saved replies | articles, replies |

## Per platform

### Zendesk (B-35, P0-1)

- Connect: Admin Center > Apps and integrations > APIs > OAuth clients > Add OAuth client,
  kind **Confidential**, scope `read`. `{"source":"zendesk","settings":{"subdomain":"<sub>"},
  "credentials":{"client_id":"<Identifier>","client_secret":"<Secret>"}}`.
- Verify: connects on a **new** account (B-35); `/oauth/tokens` answer has `expires_in`;
  attachments download with the Bearer token; `include=organizations` fills organization;
  a deleted agent's reply shows as an agent ("Deleted user"), not the customer; a ticket over
  100 comment pages is whole.

### Intercom (P0-4, P1-8, P1-17, B-42..44)

- Connect: Developer Hub > your app > Authentication, access token. Region `us`/`eu`/`au`.
- Verify: the 600-message conversation is whole and in order (API 2.18 parts paging);
  "priority" conversations are high; Ticket objects come after conversations (check a
  customer ticket isn't imported twice); macros become saved replies; article categories
  are collection names (B-44); dry-run contact counts match the run on a clean workspace
  (B-43); a file link older than 30 minutes is re-read, not lost (B-42); a note on an
  assignment stays private.

### Freshdesk (P1-16, B-28)

- Connect: Profile settings > API key. `{"source":"freshdesk","settings":{"domain":"<d>"},
  "credentials":{"api_key":"..."}}`. Archived tickets are on by default.
- Verify: archived tickets above and below the highest live id arrive; on a plan without the
  archive API the dry run says so; CSAT 403 on a plan without ratings is a warning, not a
  failure; a ticket updated during the import is still imported once; **from the test
  server**, files on S3 download (B-28 was the server's network: check egress to
  `*.s3.amazonaws.com` first).

### Gorgias (B-36, P1-18)

- Connect: Settings > REST API: `{"source":"gorgias","settings":{"domain":"<d>"},
  "credentials":{"email":"...","api_key":"..."}}`.
- Verify: a PDF arrives as the PDF, never a placeholder PNG (B-36); the dry run warns about
  Help Center articles and macros without text; sync picks up an old ticket edited today.

### Help Scout (B-37..39, P1-19)

- Connect: Your Profile > My Apps > Create App: `{"source":"helpscout","settings":{},
  "credentials":{"app_id":"...","app_secret":"...","docs_key":"<optional>"}}`.
- Verify: attachments copy (B-37); every saved reply arrives with a valid shortcut (B-38);
  a wrong secret says "check the App ID and App Secret" (B-39); cc/bcc on threads.

### HubSpot (P1-14, B-33, B-34)

- Connect: a private app's token (or a service key) with `crm.objects.tickets.read`,
  `crm.objects.contacts.read`, `crm.objects.owners.read`, `sales-email-read`, `files.read`;
  optionally calls, meetings and `conversations.read`. `{"source":"hubspot","settings":{},
  "credentials":{"token":"..."}}`.
- Verify: a ticket with 100+ emails is whole; the ticket description is kept even when it
  has emails; custom properties appear by label; calls and meetings are notes by their
  owner; without the optional scopes the report warns; a chat ticket's inbox thread is its
  conversation; a file kept as a link shows a usable address (B-34); `source_url` opens.

### Zoho Desk (B-45..49, P1-15)

- Connect: api-console.zoho.com > Self Client, scopes `Desk.tickets.READ,Desk.contacts.READ,
  Desk.basic.READ,Desk.articles.READ` (+ `Desk.search.READ` for archived). Region of the
  account (`com`, `eu`, `in`, `com.au`, `jp`, ...).
- Verify: discovery counts contacts (B-45); articles page (B-46); **one** token request for
  the whole run, and a token throttle waits instead of failing (B-47); tags arrive (B-48); an
  old ticket edited today arrives in sync (B-49); comment files copy; long threads are whole
  (`fullContentURL`); watch the day's credits (a trial has 5,000).

## CSV

Upload a file with 1,000 rows (`POST /imports/csv?filename=...`), including a bad row and a
card number: the bad row is reported in `warnings`/`rows_left_out`, the card number is
removed and a system line sits right after its message (B-32).

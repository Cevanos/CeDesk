# Migration work: progress checklist

Durable state of the consolidated migration task (started 17 Nov 2026), so work can resume
without repeating anything. Audit: `Downloads/cdesk/migration-audit-2026-11-17.md`. Live test
workbook: `Downloads/CEDESK_IMPORT_MIGRATION_TEST_RESULTS.xlsx` (16 Nov). Real-account
procedure: `docs/imports/SMOKE_TESTS.md`.

Branch `fix/import-record-failures-and-retries`. Pushed: `ea8ad13` (P0-2), `6a7da7b` (resume,
locks, sync-stop, fencing). Everything after `6a7da7b` is **local, not pushed**, until the
user approves a push.

Status words: **FIXED-in-code** (code + automated test that fails on the old code),
PARTIAL, API-LIMITED (the source's API can't), BLOCKED (external), UNVERIFIED (behaviour
of the live API assumed from docs), NOT DONE. **Nothing below is live-verified**: every test
is either mocked HTTP (unit) or real PostgreSQL with a simulated source (db).

## Live-test bugs (workbook "Bugs" sheet)

| Bug | Status | Commit | Notes |
|---|---|---|---|
| B-35 Zendesk new account can't connect | FIXED-in-code | 0c5f754 | OAuth client credentials; live retest needed |
| B-28 Freshdesk file hosts unreachable | BLOCKED (server network) | bec14cd | code side: never stalls, file kept as a link and counted; S3 reachable from dev |
| B-29 one file stalls the import | FIXED-in-code | bec14cd | 3 tries per file, then a link line + loss count; a 429 still pauses |
| B-42 Intercom files stall import/sync | FIXED-in-code | bec14cd, c2cc9b2 | same as B-29; expired links re-read once |
| B-37 Help Scout attachment path | FIXED-in-code | e71b621 | |
| B-36 Gorgias placeholder PNG | FIXED-in-code | 46effaa, 0413021 | download route; placeholder detected, `file_placeholder` |
| B-45 Zoho discovery 422 (viewId) | FIXED-in-code | c70032f | "All Contacts" view name UNVERIFIED (falls back to paging) |
| B-46 Zoho articles paging | FIXED-in-code | c70032f | |
| B-47 Zoho token refresh storm | FIXED-in-code | c70032f | token cached per account per process |
| B-48 Zoho tags | FIXED-in-code | c70032f | |
| B-49 Zoho sync of existing tickets | FIXED-in-code | c70032f | reads each ticket's own modifiedTime |
| B-38 Help Scout saved replies skipped | FIXED-in-code | e71b621, f8b79a2 | shortcut slugs ≤ 32 |
| B-32 card-number line placed late | FIXED-in-code | see git log (B-32) | line sits after the message it was taken from |
| B-34 HubSpot file link `hubspot-file:<id>` | FIXED-in-code | afeb742 | |
| B-39 Help Scout vague credentials message | FIXED-in-code | e71b621 | |
| B-43 Intercom dry-run vs run contacts | PARTIAL | db0a446, c2cc9b2 | duplicates on a page counted once; the "already imported" number comes from earlier map rows: check on a clean workspace |
| B-44 Intercom category "Collection <id>" | FIXED-in-code | c2cc9b2 | |
| B-22, B-30, B-31, B-40 | WITHDRAWN | | not reopened |

## Audit P0 / P1 / P2

| # | Item | Status | Commit | Notes |
|---|---|---|---|---|
| P0-1 | Zendesk OAuth | FIXED-in-code | 0c5f754 | client_credentials; refresh-token / browser flow NOT DONE (not needed) |
| P0-2 | Per-record error tolerance | FIXED-in-code | ea8ad13 | pushed |
| P0-3 | Loss reporting | FIXED-in-code | bbb3027, bec14cd, 45c43f7 | `outcome` + `report`; mid-run warnings too |
| P0-4 | Intercom parts paging | FIXED-in-code | c2cc9b2 | API 2.18 for the paged read; UNVERIFIED live |
| P0-5 | File-only messages | FIXED-in-code | e71b621, 0c5f754, c70032f, … | all connectors |
| P0-6 | Caps reported, not silent | FIXED-in-code | bec14cd + connectors | `history_capped`, `files_over_limit`, `archive_capped`, … |
| P0-7 | Real-account smoke tests | PARTIAL | see git log (P0-7) | procedure written; live runs need trial accounts |
| P1-8 | Intercom priority | FIXED-in-code | c2cc9b2 | |
| P1-9 | Zendesk field 0, dropdown labels, deleted agents | FIXED-in-code (deleted users PARTIAL) | 0c5f754 | permanently deleted users: role is a best guess |
| P1-10 | Sync must not overwrite CeDesk edits | FIXED-in-code | f8b79a2 | migration 0057 |
| P1-11 | Sync order (Intercom search, Zoho, Gorgias) | FIXED-in-code | c2cc9b2, c70032f, 46effaa | |
| P1-12 | CSAT on any status | FIXED-in-code | bec14cd | |
| P1-13 | Missing/invalid dates | FIXED-in-code | bbb3027 + all connectors | `date_missing` / `date_replaced` |
| P1-14 | HubSpot paging, description, properties, calls | FIXED-in-code | afeb742 | inbox threads PARTIAL; SLA API-LIMITED |
| P1-15 | Zoho tags, comment files, archived, departments | FIXED-in-code (archived PARTIAL) | c70032f | archived: ≤ 5,000 per department (API) |
| P1-16 | Freshdesk archived, id range, CSAT 403 | FIXED-in-code | 19a7a5d | archive-API answer on a live id UNVERIFIED |
| P1-17 | Intercom Tickets, macros | PARTIAL | c2cc9b2 | shared ids with conversations UNVERIFIED |
| P1-18 | Gorgias dry-run warnings | FIXED-in-code | 46effaa | articles API-LIMITED |
| P1-19 | Help Scout caps, cc/bcc | FIXED-in-code | e71b621 | ratings API-LIMITED |
| P1-20 | Stale test name | FIXED-in-code | a90a113 | |
| P2-21 | CC / participants | FIXED-in-code | 300d8e0 | `hd_message.recipients`, `source_cc` |
| P2-22 | Organizations, contact phone | FIXED-in-code | 300d8e0 | migration 0059; other_emails / attributes NOT stored |
| P2-23 | Sanitized HTML, inline images | NOT DONE | | bodies are stored as text, as before; inline images are not copied |
| P2-24 | SLA, links/merges, ticket type | PARTIAL | 300d8e0 | kept as source fields; a link to an imported ticket is a CeDesk link; CeDesk SLA not changed |
| P2-25 | Sync deletions | NOT DONE | | needs per-source tombstone reads; `message_deleted` counted where seen |
| P2-26 | Concurrent detail fetches | NOT DONE | | see the benchmark below; pages stay sequential |
| P2-27 | Old rules / automations report | NOT DONE | | nothing imported or activated (by design) |
| P2-28 | Article translations, dates | FIXED-in-code (re-run updates NOT DONE) | a90a113 | |
| P2-29 | Admin UI | NOT DONE (not in this repo) | | API contracts in `docs/api/CHANGELOG.md` |

## Other limitations from earlier tasks

| Item | Status | Commit |
|---|---|---|
| Credential update after 401/403, then resume | FIXED-in-code | c82fd9d (migration 0058) |
| Lease renewal on long pages | FIXED-in-code | fd4fa05 |
| CSV file present on resume | FIXED-in-code | a90a113 (checks storage, not only the setting) |
| Portal showed every ticket field to customers | FIXED-in-code | 6cfb7df (found during this work) |

## Performance

`scripts/bench_import.py` (simulated source, real worker, real PostgreSQL with RLS, test DB,
dev machine, 17 Nov): 100,000 tickets x 4 messages, a 20 KB file on every 20th.

| Step | Time | Rate | Check |
|---|---|---|---|
| First import | 494 s, 17 passes of ≤ 31 s | 202 tickets/s, 809 messages/s | 100,000 tickets, 400,000 messages, 5,000 files: ok |
| Same import again | 118 s | 848 tickets/s checked | no duplicates: ok |
| Sync look, 1,000 changed | 3.6 s | 276 tickets/s | 1,000 new messages: ok |

Peak memory 162 MB (flat from 2,000 to 100,000 tickets). A real source is slower than this:
the source's API rate limits, not CeDesk, set the pace (P2-26).

## Next actions

1. Real-account smoke tests (`SMOKE_TESTS.md`), Zendesk and Intercom first.
2. Then P2-23, P2-25, P2-26 if the smoke tests or the benchmark show they matter.

# Migration work: progress checklist

Durable state of the consolidated migration task (started 17 Nov 2026), so work can resume
without repeating anything. Audit: `Downloads/cdesk/migration-audit-2026-11-17.md`. Live test
workbook: `Downloads/CEDESK_IMPORT_MIGRATION_TEST_RESULTS.xlsx` (16 Nov).

Branch `fix/import-record-failures-and-retries`. Pushed: `ea8ad13` (P0-2), `6a7da7b` (resume,
locks, sync-stop, fencing). Everything below is local until the user approves a push.

Status words: DONE (code + automated test), PARTIAL, API-LIMITED, BLOCKED (external), TODO.
No item is live-verified unless it says so.

## Live-test bugs (workbook "Bugs" sheet)

| Bug | Status | Notes |
|---|---|---|
| B-35 Zendesk new account can't connect | TODO | = P0-1 OAuth |
| B-28 Freshdesk file hosts unreachable | TODO | network on the server; code side = B-29 |
| B-29 one file stalls the import | TODO | |
| B-42 Intercom files stall import/sync | TODO | |
| B-37 Help Scout attachment path | TODO | |
| B-36 Gorgias placeholder PNG | TODO | |
| B-45 Zoho discovery 422 (viewId) | TODO | |
| B-46 Zoho articles paging | TODO | |
| B-47 Zoho token refresh storm | TODO | |
| B-48 Zoho tags | TODO | |
| B-49 Zoho sync of existing tickets | TODO | |
| B-38 Help Scout saved replies skipped | TODO | |
| B-32, B-34, B-39, B-43, B-44 (low) | TODO | |
| B-22, B-30, B-31, B-40 | WITHDRAWN | not reopened |

## Audit P0 / P1 / P2

| # | Item | Status | Notes |
|---|---|---|---|
| P0-1 | Zendesk OAuth | TODO | |
| P0-2 | Per-record error tolerance | DONE | ea8ad13 |
| P0-3 | Loss reporting | TODO | |
| P0-4 | Intercom parts paging | TODO | |
| P0-5 | File-only messages | TODO | |
| P0-6 | Caps reported, not silent | TODO | |
| P0-7 | Real-account smoke tests | TODO | procedure only; live runs need accounts |
| P1-8 | Intercom priority | TODO | |
| P1-9 | Zendesk field 0, dropdown labels, deleted agents | TODO | |
| P1-10 | Sync must not overwrite CeDesk edits | TODO | |
| P1-11 | Sync order (Intercom search, Zoho, Gorgias) | TODO | |
| P1-12 | CSAT on any status | TODO | |
| P1-13 | Missing/invalid dates | TODO | |
| P1-14 | HubSpot paging, description, properties, calls | TODO | |
| P1-15 | Zoho tags, comment files, archived, departments | TODO | |
| P1-16 | Freshdesk archived, id range, CSAT 403 | TODO | |
| P1-17 | Intercom Tickets, macros | TODO | |
| P1-18 | Gorgias dry-run warnings | TODO | |
| P1-19 | Help Scout caps, cc/bcc | TODO | |
| P1-20 | Stale test name | TODO | |
| P2-21..29 | Advanced | TODO | |

## Other limitations from earlier tasks

| Item | Status |
|---|---|
| Credential update after 401/403, then resume | TODO |
| Lease renewal on long pages | TODO |
| CSV file present on resume | DONE (refused with a clear 409 in 6a7da7b) |

## Test status

Last full run: 1689 passed, 2 skipped (6a7da7b).

## Next actions

See the first TODO rows above, in order: live bugs, P0, P1, P2.

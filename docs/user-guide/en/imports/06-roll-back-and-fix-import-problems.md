---
title: Roll back and fix import problems
slug: roll-back-and-fix-import-problems
topic: imports
order: 6
summary: Go back to the old help desk if go-live goes wrong, and deal with records that could not be imported.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/imports/runs/{run_id}/failures
  - POST /api/v1/imports/runs/{run_id}/failures/{failure_id}/dismiss
  - GET /api/v1/imports/history
related:
  - imports/go-live-checklist
---

Things go wrong in moves. This article is how to go back, and how to deal with records CeDesk could not read.

> **Who can do this:** Admins.

## Roll back

Going back is safe only while the old help desk still has everything.

1. **Point email back** at the old help desk, through forwarding or the MX record. New customer email goes there again.
2. **Switch off rules and workflows in CeDesk**, so nothing is sent from it.
3. **Find tickets made or answered in CeDesk after go-live.** They are not in the old help desk. List them, for example by creation date or by tickets not tagged as imported, and copy or export them by hand. CeDesk never writes to the old help desk.
4. Fix what went wrong, then start again from running side by side. Running the import again creates no duplicates.

## Records that could not be read

If a ticket was deleted in the source after it was listed, or kept failing after every retry, CeDesk leaves it out so the rest goes on. Each shows in the run's list of failures, and stays there after the run ends. A ticket imported without all its history is listed as incomplete.

- **Import again** to bring in what has come back. The row turns resolved once the record is read in full.
- **Dismiss** a failure when you accept the loss. It records who decided. A later read does not reopen it.

## See past imports

**History** lists every import, newest first, with when, from where, by whom and the result: running, finished, syncing or failed with the last error. To see the tickets an import brought, filter the ticket list by imported source.

## Check it worked

Email arrives at the old help desk again, and the failure list is empty or dismissed.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The go-live check says there are open failures | Some records could not be read. | Import again, or dismiss each one. |
| Files are links, not attachments | A source's file host was unreachable after three tries. | The loss is counted in the report; keep the link. |

---
title: The go-live checklist
slug: go-live-checklist
topic: imports
order: 5
summary: What to check before switching, and the steps to switch.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/imports/runs/{run_id}/cutover
  - POST /api/v1/imports/runs/{run_id}/sync-stop
related:
  - imports/roll-back-and-fix-import-problems
---

Before you switch, CeDesk checks the import for you. Go live only when every check passes.

> **Who can do this:** Admins.

## The checks

| Check | If it fails |
|-------|-------------|
| The import finished | It is still running or failed. Wait, or resume it. |
| Counts match | Records the source counted are not in CeDesk. See the run's report. |
| No open failures | Some records could not be read. Import again, or dismiss each one. |
| Sync is fresh | CeDesk has not looked at the source for 15 minutes. Use **Sync now**. Skipped when not syncing. |
| Agents are matched | Messages are from agents who are not in CeDesk. Invite them. |
| Teams are set | Open imported tickets have no team, so no queue shows them. |
| Email is ready | No support address has verified forwarding, so new email will not reach CeDesk. |

## Go live

1. Point your email forwarding, or the MX record, at CeDesk. Send a test email and check it becomes a ticket.
2. Use **Sync now** for a last look. Wait for it to finish, then stop sync. CeDesk stops asking the old help desk and deletes the stored credentials.
3. Switch on your rules and workflows in CeDesk.
4. Tell agents to work only in CeDesk. Keep the old help desk read-only for a while.

## Check it worked

New customer email becomes a ticket in CeDesk, and your agents reply from CeDesk.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The checklist is not ready | One check fails. | Fix it from the table. |
| Customers got double replies | The old help desk was still answering. | Make it read-only. |

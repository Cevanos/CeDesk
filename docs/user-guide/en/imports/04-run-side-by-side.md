---
title: Run both help desks side by side
slug: run-side-by-side
topic: imports
order: 4
summary: Keep the old help desk live while CeDesk follows it, so you can test with no risk to customers.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - POST /api/v1/imports/connections/{connection_id}/run
  - POST /api/v1/imports/runs/{run_id}/sync-now
related:
  - imports/go-live-checklist
---

Instead of switching in one night, start the import with **sync** on. Your agents keep working in the old help desk while CeDesk follows it.

> **Who can do this:** Admins. Not possible for a CSV file.

## How it works

- The old help desk stays the place where agents work.
- Every 5 minutes, CeDesk brings new tickets, new messages, status changes, owners, teams and new tags.
- Imported and synced tickets **send nothing and run nothing in CeDesk**: no emails to customers, no satisfaction surveys, no rules, no routing, no notifications and no webhooks. Customers never get a reply twice.
- Changes agents make in CeDesk are not overwritten by the sync.

## What to do meanwhile

Let a few agents try CeDesk on the imported tickets. Rebuild your rules and test each in test mode.

## Ask for changes now

Use **Sync now** to look at the source at once, instead of waiting 5 minutes.

## Check it worked

A ticket created in the old help desk shows up in CeDesk within about 5 minutes.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Sync-now is refused | The run is not syncing, or its first import is still running. | Wait for the first import. |
| A change is missing | The source's change is not read yet. | Use Sync now. |
| Deleted tickets stay | Deletions in the source are not synced. | Delete them in CeDesk. |

---
title: Import from another help desk
slug: import-from-another-help-desk
topic: imports
order: 2
summary: Connect the old help desk with a key, check it, run a dry run and import in the background.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - POST /api/v1/imports/connections
  - POST /api/v1/imports/connections/{connection_id}/dry-run
  - POST /api/v1/imports/connections/{connection_id}/run
  - POST /api/v1/imports/connections/{connection_id}/credentials
  - POST /api/v1/imports/connections/{connection_id}/close
  - GET /api/v1/imports/runs/{run_id}
  - POST /api/v1/imports/runs/{run_id}/resume
related:
  - imports/run-side-by-side
---

CeDesk connects to your old help desk with a key, reads its data, and writes it into your workspace. It never writes to the old help desk.

> **Who can do this:** Admins.

## Connect

1. Go to **Settings › Channels › Imports** and select **New import**.
2. Choose the source: Intercom, Zendesk, Freshdesk, Gorgias, Help Scout, HubSpot or Zoho Desk. Each says **Connect with a key**.
3. Paste the key or token the source asks for. CeDesk checks it with the source, then stores it encrypted.

## Look first

CeDesk asks the source what it holds and which team each group probably maps to. Check the mapping, and fix it where it is wrong. Then run a **dry run**. It shows what an import would do and saves nothing.

## Import

Start the import. It runs in the background, so you can leave the page. Watch the run's progress. If you start it with sync on, see [Run both help desks side by side](run-side-by-side.md).

Running the import again creates no duplicates: tickets already in CeDesk are updated, not copied.

## If it stops

- **The source refused the key** (revoked or expired): give the connection new credentials. They must reach the same account. Then resume the run.
- **Resume** carries on from the last saved page. Nothing is made twice.
- **Close the connection** when you are done. It deletes the stored key. The import history stays. You cannot close it while a run is going.

## Check it worked

The run shows as finished. **Tickets** has tickets tagged with the source, and the counts match the dry run.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The source refuses the key | It is wrong, revoked or lacks access. | Make a new key with the access the source needs. |
| The source cannot be reached | It is down or slow. | Try again. |
| It says a run is already going | This connection has one in progress. | Wait, or stop sync first. |
| The run failed part-way | A credential problem or the source went away. | Fix it, then resume. |

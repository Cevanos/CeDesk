---
title: Plan your move to CeDesk
slug: plan-your-move
topic: imports
order: 1
summary: What can be imported, what you must set up yourself, and the steps from first check to go-live.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/imports/connections/{connection_id}/report
  - GET /api/v1/imports/connections/{connection_id}/discover
  - GET /api/v1/imports/history
related:
  - imports/import-from-another-help-desk
---

Moving from another help desk is safest in small steps: check first, import, run both help desks side by side, check again, then switch. You can go back at any step until the end.

> **Who can do this:** Admins. Every import is recorded and listed under **History**.

## What can come over

Go to **Settings › Channels › Imports**. You can import from Intercom, Zendesk, Freshdesk, Gorgias, Help Scout, HubSpot, Zoho Desk, or from a CSV file. CeDesk brings customers, tickets with their messages, help articles and saved replies, as far as the source allows.

## The steps

1. **Check** with the pre-migration report. See below.
2. **Set up** what the report says you must set up first.
3. **Dry run** to see what would happen. Nothing is saved.
4. **Import**, with sync on if you want both help desks side by side.
5. **Run side by side** while agents try CeDesk.
6. **Check again** with the go-live checklist.
7. **Go live**, and keep the old help desk read-only for a while.

Each step has its own article. Start with [Import from another help desk](import-from-another-help-desk.md).

## The pre-migration report

After you connect a source, the report lists three groups:

| Group | Meaning |
|-------|---------|
| Imports | CeDesk brings it, and shows the source's count. |
| Needs setup | CeDesk does not bring it. Set it up yourself first. |
| Cannot move | This source cannot give it to CeDesk. Keep an export if you need it. |

**Needs setup** usually means:

- **Agents.** Invite them to CeDesk before importing. They are matched by email. A message from an agent who is not in CeDesk keeps the agent's name, and the ticket stays unassigned.
- **Groups to teams.** Create a team for each group the report names, or choose one when you start the import.
- **Triggers, automations, SLA policies, views, reports, email addresses and business hours.** Rebuild them in CeDesk. Test each rule in test mode before you switch it on.

## What imported tickets look like

Imported tickets are tagged with where they came from, for example "imported-zendesk", and you can filter the ticket list by source. They are left out of reports' time numbers. Bodies are stored as text, and inline images are not copied.

## Check it worked

The report shows no surprises, and the dry run's counts match what you expect.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A group has no team | You did not map it. | Create a team and map it, or pick one at import. |
| Agents' tickets are unassigned | The agent is not a member yet. | Invite them, then import again. |

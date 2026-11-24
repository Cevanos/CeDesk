---
title: Change ticket details and edit many at once
slug: change-ticket-details-and-bulk-edit
topic: working-with-tickets
order: 4
summary: Status, priority, type, team, assignee, tags and resolving, one ticket or up to 100 at a time.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: All workspaces
routes:
  - PATCH /api/v1/tickets/{ticket_id}
  - POST /api/v1/tickets/{ticket_id}/resolve
  - POST /api/v1/tickets/bulk
related:
  - working-with-tickets/snooze-a-ticket
---

Every ticket has properties you can change at any time. Each change is recorded in the ticket's history.

> **Who can do this:** Agents, Supervisors and Admins. Moving a ticket from someone else needs the right to reassign.

## Change one ticket

Open the ticket. On the right, under **Properties**, change:

| Property | Choices |
|----------|---------|
| Status | Open, Pending or Resolved |
| Priority | Urgent, High, Medium or Low |
| Type | Your ticket types. Fields the new type lacks are cleared. |
| Team and Assignee | Any team and member. Changing the team may re-route the ticket. |

Under **Tags**, select **Add tag**. A ticket can have 20 tags of up to 60 characters each. To finish a ticket, select **Resolve**. To reopen it, set its status back to Open.

If someone changed the ticket while you looked at it, a **Reload ticket** button appears. Reload, then make your change again.

## Change many tickets at once

1. In the list, tick the tickets. Up to 100 at a time.
2. Choose the change: assign, set team, set status, set priority, add or remove tags, snooze or end a snooze.
3. Confirm.

Each ticket is checked on its own, with the same rules as a single change. If some are refused, the rest are still changed, and CeDesk lists each failure with its reason. You can make 10 bulk changes a minute.

## Check it worked

The ticket or list shows the new values, and the ticket's history has a line for the change.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "Resolve the child tickets first" and the names of those still open | The ticket has open child tickets. | Resolve the children, then resolve this one. |
| A bulk change fails for some tickets | They are outside your team or your role cannot change them. | Read the reason listed for each. |
| Too many bulk requests | More than 10 in a minute. | Wait a minute. |
| Bulk change is not offered | Your role cannot make bulk changes. | Ask an Admin. |

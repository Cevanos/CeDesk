---
title: Log time on a ticket
slug: log-time
topic: ticket-tools
order: 2
summary: Record how long you spent, mark it billable, and see the totals.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: All workspaces
routes:
  - GET /api/v1/tickets/{ticket_id}/time
  - POST /api/v1/tickets/{ticket_id}/time
  - DELETE /api/v1/time/{entry_id}
related:
  - ticket-tools/tasks
---

Time entries show how much effort a ticket took. They feed the time numbers in reports.

> **Who can do this:** Agents, Supervisors and Admins.

## Log time

1. Open the ticket. In the side panel, under **More**, find **TIME**.
2. Enter the minutes, from 1 to 1,440 (one day) for one entry.
3. Add a note if you like, up to 500 characters.
4. Tick **Billable** if the customer is charged for it.
5. Select **Log time**.

**TIME** shows the total and the billable total, and each entry, newest first.

## Remove an entry

You can remove your own entries. An Admin can remove anyone's.

## Check it worked

The totals under **TIME** go up by the minutes you logged.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "Log between 1 and 1440 minutes." | The number is outside the range. | Split long work into several entries. |
| "You can remove only your own time entries." | The entry is someone else's. | Ask an Admin. |

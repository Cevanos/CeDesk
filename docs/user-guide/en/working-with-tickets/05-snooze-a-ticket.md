---
title: Snooze a ticket
slug: snooze-a-ticket
topic: working-with-tickets
order: 5
summary: Put a ticket to sleep until a time, and what wakes it.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: All workspaces
routes:
  - POST /api/v1/tickets/{ticket_id}/snooze
  - DELETE /api/v1/tickets/{ticket_id}/snooze
related:
  - working-with-tickets/change-ticket-details-and-bulk-edit
---

Snooze a ticket when you are waiting for something, such as a delivery, and want it out of your way until then.

> **Who can do this:** Anyone who can change the ticket.

## Snooze it

1. Open the ticket and select **Snooze**.
2. Choose when it should come back. The time must be at least a minute ahead and at most a year.
3. Confirm.

The ticket's status becomes Pending. At the chosen time it goes back to Open, and the assignee and followers are told.

## What wakes it early

- A reply from the customer.
- You ending the snooze: open the ticket and end it, and it reopens now.

Snoozing again moves the time.

## Find snoozed tickets

Use the **Snoozed** tab, or tick **Hide snoozed** to leave them out of the list.

## Check it worked

The ticket shows as Pending with the wake-up time, and appears under **Snoozed**.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "A resolved ticket can't be snoozed. Reopen it first." | The ticket is Resolved. | Set it to Open, then snooze. |
| The time is refused | It is in the past, under a minute ahead or over a year. | Pick another time. |

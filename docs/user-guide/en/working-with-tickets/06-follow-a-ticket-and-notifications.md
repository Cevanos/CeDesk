---
title: Follow a ticket and read notifications
slug: follow-a-ticket-and-notifications
topic: working-with-tickets
order: 6
summary: Get told about a ticket you do not own, add a colleague as a follower, and clear your notifications.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: All workspaces
routes:
  - POST /api/v1/tickets/{ticket_id}/followers
  - DELETE /api/v1/tickets/{ticket_id}/followers/{member_id}
  - GET /api/v1/notifications
  - POST /api/v1/notifications/read-all
related:
  - working-with-tickets/reply-and-add-private-notes
---

Followers hear about a ticket without owning it. Use it for a ticket that matters to you, or to keep a colleague in the loop.

> **Who can do this:** Anyone who can see the ticket can follow it. Adding someone else needs the right to change the ticket.

## Follow

Open the ticket and select **Follow**. Select it again to stop. Followers are told about public replies and status changes.

## Add a colleague

1. In the ticket's side panel find **Followers** and select **Add follower**.
2. Choose the person.

The person must be an active member whose role can see the ticket. Adding the same person twice changes nothing. Adding and removing followers is recorded in the audit log.

## Notifications

The bell at the top shows how many unread notifications you have. Each says what happened and links to the ticket. Open one to mark it read, or mark them all read. You are also notified when someone mentions you in a private note, or when a snoozed ticket you follow wakes up.

The **My work** button next to the bell counts the open tickets assigned to you.

## Check it worked

The ticket's **Followers** list shows the person, and they receive the next public reply notification.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "That person isn't a member of this workspace." | They are not on your team list. | Add them under Members first. |
| The person is deactivated | They cannot follow tickets. | Reactivate them or choose someone else. |
| Their role can't see this ticket | Their role or team does not allow it. | Choose someone who can. |

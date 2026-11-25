---
title: Retry failed emails and events
slug: retry-failed-emails-and-events
topic: channels
order: 3
summary: See replies that could not be delivered and messages to other systems that failed, and send them again.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/email/failed
  - POST /api/v1/email/{email_id}/retry
  - GET /api/v1/outbox/failed
  - POST /api/v1/outbox/{event_id}/retry
related:
  - channels/support-email-addresses
---

CeDesk tries again by itself when it cannot send something. When every try fails, it lists the item here so you can fix the cause and send it again.

> **Who can do this:** Admins.

## Open the list

Go to **Settings › Channels › Failed events**. It has two lists.

**Failed emails** are replies that could not be delivered after several tries. Each shows the subject, ticket, recipient, number of tries, and the answer the mail server gave. Select **Send again** to queue it again. It starts again from zero tries and goes out within seconds. The ticket gets a line saying who did it.

**Failed events** are messages to other systems that did not go through, such as a reply to a Cevanos chat while Cevanos could not be reached, or an alert to Slack. Each shows the event, when it failed and why. Select **Send again** to try again now. Steps that already worked are not repeated. **Open ticket** takes you to the ticket.

## What cannot be retried

Account emails, such as a password reset, are not kept when they fail, because they hold a secret link. The person asks for a new link instead.

## Check it worked

The item leaves the list, and the ticket shows the reply as sent.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| It fails again with the same error | The cause is still there. | Fix it, for example the recipient address, your DNS, or the connection to the other system, then retry. |
| "The mail server said 552" | The recipient's server refused it, often a full mailbox. | Contact the customer another way. |
| A Cevanos event fails because there is no customer to reply to | The chat was a test chat. | Safe to ignore. |

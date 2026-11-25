---
title: Side conversations
slug: side-conversations
topic: ticket-tools
order: 4
summary: Ask someone outside the ticket, by email or in a Slack channel, without the customer seeing it.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: All workspaces
routes:
  - GET /api/v1/tickets/{ticket_id}/side-conversations
  - POST /api/v1/tickets/{ticket_id}/side-conversations
  - POST /api/v1/tickets/{ticket_id}/side-conversations/slack
  - POST /api/v1/side-conversations/{side_id}/messages
related:
  - ticket-tools/jira-issues
---

Sometimes you need an answer from a supplier or a colleague who is not in CeDesk. A side conversation lets you ask, and keeps their reply on the ticket. The customer never sees it.

> **Who can do this:** Agents, Supervisors and Admins who can reply on the ticket.

## Start one by email

1. Open the ticket. Under **More**, find **SIDE CONVERSATIONS** and select **New side conversation**.
2. Choose email, then the addresses, a subject and your message.
3. Send.

It goes from the ticket's support address. Their reply comes back to the ticket, and the assignee is told.

## Start one in Slack

An Admin first installs **Slack side conversations** under **Settings › Developers › Apps**, and invites the bot to the channel.

Choose Slack, pick the channel and write your message. The bot posts it with the ticket's number and subject, never the customer's messages. Replies in that thread come back to the ticket.

## Write again

Open the side conversation and add a message. For email, it is threaded under the earlier emails to the same addresses. For Slack, it is another message in the same thread.

## Limits

20 side conversations per ticket.

## Check it worked

The side conversation shows under **SIDE CONVERSATIONS**, and the ticket has a line saying it was started.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| It is refused for lack of a support address or the Slack app | Neither is set up. | Ask an Admin. |
| 20 already | The ticket reached the limit. | Reuse an existing one. |
| Slack did not answer | Slack was slow. | Try again. |
| No Slack post appears | The bot is not in the channel. | Invite it, then send again. |

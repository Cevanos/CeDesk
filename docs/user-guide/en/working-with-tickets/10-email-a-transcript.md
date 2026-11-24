---
title: Email a transcript
slug: email-a-transcript
topic: working-with-tickets
order: 10
summary: Send the customer, or someone else, a copy of the conversation.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: All workspaces
routes:
  - POST /api/v1/tickets/{ticket_id}/transcript
related:
  - working-with-tickets/reply-and-add-private-notes
---

A transcript is an email with the conversation so far. Use it when a customer asks for a copy.

> **Who can do this:** Anyone who can change the ticket and has the right to send transcripts.

## Send it

1. Open the ticket and select **More actions**.
2. Choose **Email transcript**.
3. Send it to the customer, or type another address.

## What is in it

The customer's messages, your replies and the AI agent's lines, oldest first, with attached files listed by name. Private notes, system lines and side conversations are never included. It is sent from your default support address. A line on the ticket records that it was sent, without the text.

## Limits

10 transcripts an hour per person and 100 an hour per workspace.

## Check it worked

A line on the ticket says the transcript was sent.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| It is refused for lack of a support address | No default support address is set. | Ask an Admin to set one. |
| Nothing to send | The customer has seen no message yet. | Wait for a reply. |
| A chat or demo customer has no address | There is nowhere to send it. | Type an address of your own. |
| Too many transcripts | You hit the hourly limit. | Wait. |

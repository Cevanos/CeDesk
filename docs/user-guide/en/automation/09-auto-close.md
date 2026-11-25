---
title: Auto-close
slug: auto-close
topic: automation
order: 9
summary: Resolve Pending tickets by themselves when the customer stops replying.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/auto-close
  - PUT /api/v1/auto-close
related:
  - automation/how-automation-works
---

Some customers never answer your last question. Auto-close resolves those tickets after a wait, so your Pending list stays honest.

> **Who can do this:** Admins. It is off until you turn it on.

## Turn it on

1. Go to **Settings › Service › Auto-close**.
2. Turn on **Resolve Pending tickets when the customer doesn't reply**.
3. Set **After how many days**, counted from the customer's last reply.
4. Optionally write a **Message to send before closing**, up to 5,000 characters. It is sent as a public reply, and placeholders such as `{{customer.first_name}}` work.
5. Select **Save**.

It runs once a day. Each ticket that has been Pending with no reply and no other change for that many days is resolved, with a line on the ticket. The notice goes first, and the usual satisfaction survey goes after.

## What is never closed

Snoozed tickets, chat handovers waiting to be accepted, tickets with an approval waiting, tickets with an open child ticket, and merged and imported tickets.

## Check it worked

The next day, a test ticket left Pending beyond the limit is Resolved and has a line saying why.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A ticket was not closed | It is one of the kinds never closed, or something else changed on it. | Check the list above. |
| The customer got no notice | The message was empty. | Write one in the setting. |

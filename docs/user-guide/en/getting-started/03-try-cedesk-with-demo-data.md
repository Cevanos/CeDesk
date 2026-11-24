---
title: Try CeDesk with demo data
slug: try-cedesk-with-demo-data
topic: getting-started
order: 3
summary: Fill an empty workspace with sample tickets to learn the screens, then reset or remove them.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces, where demo mode is on
routes:
  - POST /api/v1/demo/seed
  - POST /api/v1/demo/samples
  - POST /api/v1/demo/reset
related:
  - getting-started/your-plan-and-usage
---

A new workspace has no tickets, so there is nothing to practise on. Demo data adds example tickets that you can answer, assign and close without any customer seeing it.

> **Who can do this:** Admins. Demo mode must be on for your server, and it is never available on a production server.

## What you get

| Choice | What it adds |
|--------|--------------|
| Load demo data (16 tickets) | The 6 sample tickets, 5 past tickets that are resolved and rated, 4 live tickets assigned to you with a reply, a note, a task and time logged, and 1 chat handover waiting for you to accept. |
| Load sample tickets (6) | Only the 6 sample tickets: new tickets from customers on email, chat and the web. |

Demo tickets are marked **Demo**. They go through your rules, routing and response-time targets just like real ones, so you can see those work. Nothing about them is emailed, sent to a webhook or posted to Slack.

## Load it

1. Go to **Settings › Security & data › Demo data**.
2. Select **Load demo data (16 tickets)**, or **Load sample tickets (6)**.
3. Open **Tickets**. The new tickets are there.

You can load them only once. If they are already there, use reset.

## Start again

1. Go to **Settings › Security & data › Demo data**.
2. Select **Reset demo**.

Reset adds a fresh set of demo tickets and then deletes the old ones, with their messages, notes, tasks, time, ratings and files. Tickets you made by hand, imported or received from a channel stay, and so do your members and settings. If anything fails, the old demo tickets stay as they were. Every load and reset is recorded in the audit log.

There is no button that only removes demo tickets: **Reset demo** always loads a new set. To get rid of them for good, resolve them and let your retention setting delete them, or delete them from **Tickets**.

## Check it worked

**Tickets** shows tickets marked **Demo**, and the count matches what you chose.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| There is no Demo data page, or the buttons are refused | You are not an Admin, demo mode is off, or the server is a production one. | Ask whoever runs your server. They turn on `CEDESK_DEMO_ENABLED` on a test server. |
| Loading is refused because the tickets are already there | The demo or sample tickets exist. | Use **Reset demo**. |

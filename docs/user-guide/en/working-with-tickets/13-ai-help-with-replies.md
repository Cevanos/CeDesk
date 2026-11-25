---
title: AI help with replies
slug: ai-help-with-replies
topic: working-with-tickets
order: 13
summary: Summarise a ticket, suggest or improve a reply and translate, with Cevanos connected.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: Workspaces connected to Cevanos
routes:
  - POST /api/v1/tickets/{ticket_id}/summary
  - POST /api/v1/tickets/{ticket_id}/draft
  - POST /api/v1/assist/improve
  - POST /api/v1/assist/translate
related:
  - working-with-tickets/reply-and-add-private-notes
---

When your workspace is connected to Cevanos, buttons on the ticket use AI. The AI only suggests. Nothing is sent to the customer until you send it, and CeDesk stores none of what the AI returns.

> **Who can do this:** People, not API keys, with a role that allows it. If Cevanos is not connected, the buttons are not shown.

## The buttons

| Button | What it does |
|--------|--------------|
| **Summarise** | Shows a short summary of the conversation, including internal notes. Card numbers are taken out. |
| **Suggest reply** | Puts a drafted reply in the reply box. It uses the conversation without private notes. Edit it, then send. |
| **Improve** | Rewrites your text in a tone: professional (default), friendly, empathetic or concise. |
| **Translate** | Translates a message you received, or your reply, into another language. |

## Check it worked

The summary or text appears. A note tells you when nothing changed or there was nothing to translate.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The buttons are missing | Cevanos is not connected. | Ask an Admin to connect it in Settings. |
| It says Cevanos is not connected, or lacks a permission | The connection is missing or its key lacks a scope. The message names the scope. | Ask an Admin to fix the connection. |
| "Cevanos didn't translate this. Try again later." | Cevanos is slow or returned the text unchanged. | Try again later. |
| A slow or unavailable AI | Cevanos is down. | Try again. |

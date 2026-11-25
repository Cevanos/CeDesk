---
title: Live updates (server-sent events)
slug: live-updates
topic: developers
order: 4
summary: Open a stream that tells your app something changed, then fetch it through the normal API.
status: draft
updated: 2026-11-24
who: Developers
plan: All workspaces
routes:
  - POST /api/v1/stream/ticket
  - GET /api/v1/stream
related:
  - developers/webhooks-guide
---

A stream pushes small "something changed" messages to a signed-in person's screen, so the ticket list updates without polling. It uses server-sent events (SSE). The same is true for customers in the portal.

> **Who can do this:** A developer building a screen for signed-in agents. Use webhooks for server-to-server.

## What a stream sends, and does not

An event says **that** something changed, never **what it says**. It carries ids and the names of what changed. It never carries a subject, a message text, an address or a customer's name. Your app fetches the ticket through the normal API, which applies the same permissions as always. So a stream can show nobody more than they could already open, and a missed event costs one refetch.

## Open a stream

A browser's `EventSource` cannot send an `Authorization` header, so opening a stream takes two steps.

1. `POST /stream/ticket` with your usual token. You get a one-use ticket that works for about a minute.
2. `GET /stream?ticket=…` opens the stream.

```
const { ticket } = await (await fetch(`${base}/api/v1/stream/ticket`, {
  method: "POST",
  headers: { Authorization: `Bearer ${token}` },
})).json();
const events = new EventSource(`${base}/api/v1/stream?ticket=${ticket}`);
events.addEventListener("ticket.updated", (e) => {
  const { ticket_id, changes } = JSON.parse(e.data);
  refetchTicket(ticket_id);
});
```

The session token never goes in an address.

## The events

| Event | When |
|-------|------|
| `ticket.created`, `ticket.updated`, `ticket.resolved` | A ticket was made, changed or resolved. `ticket.updated` lists the names of the changed fields. |
| `message.created` | A message was added. It has the message id and kind. |
| `assignment.changed` | A ticket was assigned. |
| `chat.accepted`, `chat.taken_over`, `chat.closed`, `chat.reopened` | The state of a Cevanos chat changed. |
| `notification` | A notification for this person. |

A role limited to one team gets only that team's events. A portal customer's stream, at `/portal/stream`, carries only customer, agent and AI messages on their own tickets and status changes, never a note or a system line.

## Stay in step

- Every event has an `id`. After a reconnect, send the last one you saw as `Last-Event-ID` so you lose nothing. Events are kept for 24 hours.
- An id can repeat after a reconnect. Ignore one you have seen.
- `reset` means your last id is older than what is kept. Refetch what you show.
- `end` means you were signed out. Stop.
- A line that starts with `:` is a heartbeat, sent about every 15 seconds.
- A browser reconnects after about 3 seconds by itself.

## Limits

You can have 5 streams open per person. A sixth is refused with 429. The ticket for opening a stream is refused with 401 if it is unknown, used, expired, or its sign-in ended.

## Check it worked

Change a ticket in another window. `ticket.updated` arrives within a couple of seconds.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| 401 opening the stream | The ticket was used or is over a minute old. | Ask for a new one each time you connect. |
| 429 opening the stream | Five streams are open already. | Close one. |
| Events stop arriving behind a proxy | The proxy buffers. | Turn off buffering and compression for this path. |

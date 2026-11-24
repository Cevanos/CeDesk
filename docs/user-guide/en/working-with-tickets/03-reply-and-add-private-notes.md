---
title: Reply and add private notes
slug: reply-and-add-private-notes
topic: working-with-tickets
order: 3
summary: Answer the customer, leave a note only your team sees, mention a colleague and attach files.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: All workspaces
routes:
  - POST /api/v1/tickets/{ticket_id}/messages
  - POST /api/v1/tickets/{ticket_id}/attachments
related:
  - working-with-tickets/saved-replies
---

A ticket has two kinds of message. A **reply** goes to the customer. A **private note** is for your team and the customer never sees it.

> **Who can do this:** Agents, Supervisors and Admins. Light agents can add notes but cannot reply.

## Reply to the customer

1. Open the ticket.
2. Select **Reply**.
3. Type your answer. Type `/` to insert a saved reply (see [Saved replies](saved-replies.md)).
4. Select **Send reply**.

CeDesk fills in these placeholders when it sends: `{{customer.first_name}}`, `{{customer.name}}`, `{{agent.name}}` and `{{fields.<key>}}`, where the last is a ticket field. A value that is empty shows as a dash.

## Add a private note

1. Select **Private note**.
2. Type the note. To tell a colleague, mention them with @. They get a notification.
3. Save it.

## Attach a file

Select **Upload file (max 25 MB)** and choose the file, then send. With a file attached, the message can be empty. Files are listed under **Files** on the ticket.

## Check it worked

Your message appears in the conversation. A reply shows as sent to the customer; a note is marked private.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| There is no **Reply** | You are a Light agent, or a chat handover is not accepted yet. | Add a note instead, or [accept the chat](accept-a-chat-handover.md). |
| The upload is refused | The file is over the size limit, 25 MB by default. | Send a smaller file or a link. |

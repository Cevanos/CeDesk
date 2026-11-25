---
title: Jira issues on a ticket
slug: jira-issues
topic: ticket-tools
order: 5
summary: Create a Jira issue from a ticket, or link an existing one.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: All workspaces, with the Jira app installed
routes:
  - GET /api/v1/tickets/{ticket_id}/jira
  - POST /api/v1/tickets/{ticket_id}/jira
  - POST /api/v1/tickets/{ticket_id}/jira/link
  - DELETE /api/v1/tickets/{ticket_id}/jira/{key}
related:
  - ticket-tools/side-conversations
---

When a ticket needs engineering work, link it to a Jira issue so both sides see each other.

> **Who can do this:** Agents, Supervisors and Admins who can reply on the ticket. An Admin installs the Jira app first.

## Install the app (Admins)

Go to **Settings › Developers › Apps**, find **Jira** and select **Install**. Enter the details it asks for. Secrets are stored safely and never shown again.

## Create or link an issue

Open the ticket. Under **More**, find **JIRA** and select **Add Jira issue**.

- **Create** makes a new issue of type Task in the project set for the app. The ticket gets a line about it.
- **Link** attaches an issue that already exists. Give its key.

A ticket can have up to 10 issues.

## Remove one

Take an issue off the ticket. It stays in Jira.

## Check it worked

The issue key shows under **JIRA** with a link to Jira.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| It is refused because Jira is not installed | The app is not installed. | Ask an Admin to install it. |
| Its saved token cannot be read | The server key changed. | An Admin installs the app again. |
| The key is already on this ticket | It is linked already. | Nothing to do. |
| No such issue | The key is wrong, or it is in another site. | Check the key. |
| Jira did not answer | Jira was slow. | Try again. |

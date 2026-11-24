---
title: Connect Slack
slug: connect-slack
topic: integrations
order: 4
summary: Post urgent tickets to a channel, and ask people in Slack from a ticket.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - PUT /api/v1/apps/{app}
  - POST /api/v1/slack/events/{workspace_id}
related:
  - integrations/install-apps
---

CeDesk works with Slack in two separate ways. Install either or both.

> **Who can do this:** Admins.

## Slack alerts

Slack alerts posts Urgent and High tickets to a channel.

1. In Slack, make an incoming webhook for the channel. You get an address that starts with https://hooks.slack.com/services/.
2. Go to **Settings › Developers › Apps**, select **Install** next to **Slack alerts** and paste the address.
3. Save. CeDesk posts a test line to the channel.

## Slack side conversations

This lets agents ask a question in a Slack channel from a ticket. See "Side conversations" in Ticket tools.

1. In Slack, make a Slack app. Give its bot the scopes `chat:write`, `channels:history`, `groups:history`, `users:read` and `files:read`. Only a shared file's name is read, never the file.
2. Install the app in your Slack. Copy its **bot token** (starts with xoxb-) and its **signing secret**.
3. In CeDesk, select **Install** next to **Slack side conversations** and paste both. Save.
4. In the Slack app's **Event Subscriptions**, set the **Request URL** to your CeDesk address followed by `/api/v1/slack/events/` and your workspace ID. Subscribe the bot to message.channels and message.groups.
5. Invite the bot to each channel agents should use.

The bot posts the ticket's number and subject, never the customer's messages. Replies in the thread come back to the ticket. Slack's own signature is checked on every request.

## Check it worked

Alerts: the test line appears in the channel. Side conversations: start one from a ticket and the message appears in the channel.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| No post in the channel | The bot is not in the channel. | Invite it. |
| Replies do not come back | The Request URL or event subscriptions are wrong. | Check both. |
| Slack did not answer | Slack was slow. | Try again. |

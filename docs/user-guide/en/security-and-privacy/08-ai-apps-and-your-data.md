---
title: AI apps and your data
slug: ai-apps-and-your-data
topic: security-and-privacy
order: 8
summary: What a connected AI app such as Claude or ChatGPT can receive from CeDesk, what CeDesk keeps about each connection, and how to end one.
status: draft
updated: 2026-11-27
who: Anyone
plan: All workspaces
routes:
  - GET /api/v1/oauth/grants
  - DELETE /api/v1/oauth/grants/{grant_id}
  - POST /oauth/revoke
related:
  - integrations/connect-ai-apps
  - security-and-privacy/audit-log
---

When a member connects an AI app to CeDesk, the app receives the help desk data that member asks it about. This page says what that data is, what CeDesk keeps about the connection, and how to end it. It is the text for the AI-apps part of a privacy policy.

> **Who this is for:** Every member who connects an app, and the Admins and data protection officers who answer for the workspace's data.

## Who decides what is shared

- An app is connected only by a member, signed in as themselves, who approves it on CeDesk's consent page. An Admin can't connect an app for someone else.
- The app acts as that member, with that member's role and ticket scope. It receives only what the member could see in CeDesk.
- The app asks for data when the member asks it something. CeDesk does not send data to an app on its own.

## What the tools can return to the app

| Area | What the app can receive |
|------|--------------------------|
| Tickets | Number, subject, status, priority, channel, team, assignee name, tags, custom field values, times created, updated and resolved, the SLA, and a link to the ticket. |
| Messages | The latest 50 messages of a ticket, customer replies, agent replies and private notes, with each author's name and time. Each message is cut at 4,000 characters. |
| Customers | The customer's name and email address, whether they are a VIP, and how many other tickets they have. |
| Reports | Ticket counts, response and resolution times, CSAT, backlog and SLA figures, tickets by channel, topic and sentiment, and figures per team and per agent (with agent names), for members who may view reports. |
| Setup | Team names and their members' ids, macros, SLA policies, automation rules (for members who may change settings), and each agent's name, role, status, channels, capacity, skills and open ticket count. |

The app never receives: passwords, two-step codes or sign-in tokens of anyone, members' email addresses, attachments and files, API keys, webhook secrets, security and single sign-on settings, privacy requests, the audit log, or the whole contact list.

Message text is written by customers. CeDesk tells the app to treat it as data, not as instructions.

## What happens to the data in the app

Data the app receives is handled by the app's provider (for example Anthropic for Claude, OpenAI for ChatGPT) under that provider's own terms and privacy policy, and the member's or organisation's agreement with them. That includes whether the provider keeps conversations, for how long, and whether it uses them to train models. CeDesk can't take back data once an app has received it. Admins who don't want customer data in an AI app should agree that with their members before they connect one.

## What CeDesk keeps about each connection

For each connected app CeDesk keeps:

| Kept | Why |
|------|-----|
| The app's name, web address, logo address and the addresses it returns to after sign-in | To show it on the consent page and in Connected apps, and to send the member back only to the app. |
| The member and workspace it belongs to | The app acts as that member, in that workspace only. |
| What the member approved: read, or read and changes | To refuse anything else. |
| When it was connected, when it was last used, and when it was disconnected | Shown in Connected apps. |
| Whether it was approved after a single sign-on sign-in | Single sign-on then counts as two-step sign-in for that connection. |
| Fingerprints (SHA-256 hashes) of its sign-in codes and tokens, with their expiry times | To check a token without storing it. The codes and tokens themselves are never stored and can't be read back. |

Sign-in codes last 60 seconds and can be used once. Access tokens last 1 hour; renewal tokens last 30 days and are replaced on every use. Ended codes and tokens are deleted a day after they end.

Changes the app makes are in the [audit log](audit-log.md) as "(member) via (app)", like any change the member makes, and approving and disconnecting an app are recorded there too. The audit log keeps the change, not the data the app read.

## How to end a connection

- **The member:** **My account › Connected apps › Disconnect**. Admins also find their own apps under **Settings › Developers › Connected apps**. See [Connect AI apps](../integrations/connect-ai-apps.md).
- **The app itself** can end its own tokens (most apps do when you remove CeDesk from them).
- **An Admin** ends every connection of a member by deactivating the member. An Admin can't see or disconnect another member's apps one by one.
- **Self-hosted:** the server's administrator can turn every connection off with CEDESK_MCP_ENABLED.

A disconnected app stops at once: its next request is refused. Disconnecting does not delete data the app already received; ask the app's provider for that.

## Check it worked

After **Disconnect**, the app is gone from **Connected apps**, and its next request asks you to connect again.

## Related

- [Connect Claude, ChatGPT and other AI apps](../integrations/connect-ai-apps.md)
- [The audit log](audit-log.md)

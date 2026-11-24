---
title: Install apps
slug: install-apps
topic: integrations
order: 2
summary: The apps you can connect, what each needs and adds, and how to install and remove them.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/apps
  - PUT /api/v1/apps/{app}
  - DELETE /api/v1/apps/{app}
related:
  - integrations/connect-slack
---

Apps add information or buttons to your tickets from other tools.

> **Who can do this:** Admins.

## The apps

Go to **Settings › Developers › Apps**.

| App | What it adds | What it needs |
|-----|--------------|---------------|
| Shopify | The customer's latest orders in the ticket sidebar. Read only. | Your shop name, and an Admin API access token with read orders and read customers. |
| Stripe | The customer's latest payments in the sidebar. Read only. | A restricted key with read access to Customers and Charges. A full secret key also works but can do far more than CeDesk needs. |
| Jira | Create or link Jira issues from a ticket. | Your Jira site, an email, a project key such as SUP, and an Atlassian API token. |
| Slack alerts | Urgent and High tickets posted to a Slack channel. | A Slack incoming webhook address. |
| Slack side conversations | Ask people in Slack from a ticket. | A Slack bot token and signing secret. See [Connect Slack](connect-slack.md). |
| Calendly | A `/book` saved reply with your booking link. | Your Calendly link. No secret. |

## Install

1. Select **Install** next to the app and fill in the form.
2. Select **Save**.

CeDesk checks the credential with the provider first, then stores it encrypted. Afterwards only the last four characters show. If the provider does not answer, try again.

## Change or uninstall

Select **Settings** to change an installed app, or **Uninstall** to remove it and delete its credential.

## When it says Unavailable

Shopify and Stripe show **Unavailable** while Cevanos is connected, because Cevanos supplies orders and payments. See [Connect Cevanos](connect-cevanos.md).

## Check it worked

The app shows **Installed** with the end of its credential, and its card or button appears on a ticket.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The credential is refused | The provider rejected it. | Make a new one with the access the form describes. |
| The provider did not answer | It was slow. | Try again. |
| An app is Unavailable | Cevanos is connected. | See above. |

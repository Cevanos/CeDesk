---
title: Get ready to send campaigns
slug: get-ready-to-send-campaigns
topic: campaigns
order: 2
summary: Your postal address, a verified sender, https link addresses, and the checks shown before you can send.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/campaigns/settings
  - PUT /api/v1/campaigns/settings
  - GET /api/v1/campaigns/{campaign_id}
related:
  - campaigns/what-email-campaigns-are
---

A campaign can only be sent when a few things are in place. The campaign screen lists what is still missing under **Before you can send**, so you can also just open a draft and read the list. This article explains each item.

> **Who can do this:** Admins. The server settings in the last section need whoever runs your CeDesk server.

## The checklist

| Item | What to do |
|------|-----------|
| A subject | Type one, at most 200 characters. |
| The email text | Write the body, at most 50,000 characters. |
| A segment | Pick who it goes to. |
| A sender | Pick the support address it comes from. |
| A verified sender | Run the forwarding check for that address. |
| SPF and DKIM passing | Run the DNS check for that address's domain. |
| Your postal address | Add it once for the workspace. |

## Add your postal address

Every campaign email shows your postal address in its footer, and no campaign can be sent without one.

1. Go to **Campaigns** and select **Settings**.
2. Type your postal address, 10 to 300 characters, for example "Acme Pvt Ltd, 12 MG Road, Bengaluru 560001, India".
3. Save.

## Verify the sender and its domain

Campaign mail comes from one of your support addresses.

1. Go to **Settings › Channels › Email** and open the address.
2. Run **check forwarding**. This makes sure bounces come back to CeDesk.
3. Run **check DNS**. SPF and DKIM must both pass.

If DNS is not right, campaign mail lands in spam, and that can hurt your normal support email from the same domain too.

## For whoever runs the server

The unsubscribe link needs a public address for the customer site, and the one-click unsubscribe and open image need one for the API. Outside a development setup both must start with https. Set `CEDESK_PORTAL_URL` and `CEDESK_API_URL`; the settings reference lists both.

## Check it worked

Open a draft campaign. The **Before you can send** box is gone and **Send** is available.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "Verify the support address first" | The forwarding check has not passed. | Run it under Settings › Channels › Email. |
| "The support address's domain needs SPF and DKIM passing" | The last DNS check did not find both. | Fix the DNS records, then run the check again. |
| "Add your postal address" | None is saved. | Campaigns › Settings. |
| "Unsubscribe links need an https address" | The server's public addresses are not https. | Ask whoever runs the server. |

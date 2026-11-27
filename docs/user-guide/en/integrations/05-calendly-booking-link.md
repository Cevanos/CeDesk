---
title: Calendly booking link
slug: calendly-booking-link
topic: integrations
order: 5
summary: Give agents a /book saved reply that sends your Calendly link.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - PUT /api/v1/apps/{app}
related:
  - integrations/install-apps
---

With Calendly, an agent types `/book` in a reply and your booking link is inserted.

> **Who can do this:** Admins.

## Install

1. Go to **Settings › Developers › Apps** and select **Install** next to **Calendly**.
2. Paste your Calendly page, or one event's page, for example https://calendly.com/acme/30min. There is no password, because the link is public.
3. Save.

CeDesk adds a saved reply `/book` with your link. If you already have a `/book`, the new one is `/book1`. Uninstalling removes it.

## Check it worked

Type `/book` in a reply box. Your link appears.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The link in `/book` is old | The saved reply was edited by hand, so CeDesk did not change it. | Edit the saved reply. |

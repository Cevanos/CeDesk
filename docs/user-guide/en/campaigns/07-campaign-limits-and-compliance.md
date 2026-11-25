---
title: Campaign limits and compliance
slug: campaign-limits-and-compliance
topic: campaigns
order: 7
summary: Recipient caps, the daily cap and sending speed, and your legal duties.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/campaigns/{campaign_id}/audience
  - POST /api/v1/campaigns/{campaign_id}/send
related:
  - campaigns/send-or-schedule-a-campaign
---

CeDesk limits how much campaign mail one workspace can send, to protect your sender reputation and everyone else's. It also puts the required footer on every email. The rules about whom you may email are still yours to follow.

> **Who can do this:** Admins read this. Whoever runs your server can change the numbers.

## The limits

| Limit | Default |
|-------|---------|
| People in one campaign | 5,000 |
| Campaign emails per workspace in 24 hours | 10,000 |
| Emails sent per minute | 60 |
| Test emails per hour, per Admin | 20 |
| Campaigns in a workspace, drafts and sent together | 1,000 |
| Scheduling | At most 90 days ahead |
| Subject | 200 characters |
| Body | 50,000 characters |

The daily limit counts emails already sent in the last 24 hours plus those still waiting in other campaigns. If a campaign would go over, it is refused with a message that says how many more can go out. A self-hosted server can change the first three numbers with `CEDESK_CAMPAIGN_MAX_RECIPIENTS`, `CEDESK_CAMPAIGN_DAILY_CAP` and `CEDESK_CAMPAIGN_PER_MINUTE`.

## What CeDesk does for you

- Every email has a footer with who sent it, your postal address and an unsubscribe link.
- The email also carries the one-click unsubscribe button that mail apps show.
- People who unsubscribe, report spam or bounce are never emailed again by a campaign.
- Erased customers are never emailed. Only a scrambled form of their address is kept so they stay off the list.

## What you must do

CeDesk is not legal advice. The law where your customers live decides when you may send them marketing email, for example whether you need their consent first. Send only to people you are allowed to email, keep your postal address correct, and honour requests to stop.

## Check it worked

Open a draft and read the **Recipients** box. It shows how many are in the segment, how many are left out and how many will receive it, and warns if a limit is in the way.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "Over the daily limit" | You hit the 24-hour cap. | Wait, or split the campaign across days. |
| "The most is 5000" | The segment is bigger than the per-campaign limit. | Narrow the segment or send in parts. |
| "No one to send to" | Everyone is left out. | Check the segment and the suppression list. |

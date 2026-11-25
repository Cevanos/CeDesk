---
title: Campaign results and tracking
slug: campaign-results-and-tracking
topic: campaigns
order: 6
summary: Sent, bounced, skipped, opened and unsubscribed, per campaign and per recipient.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/campaigns/{campaign_id}/stats
  - GET /api/v1/campaigns/{campaign_id}/recipients
  - GET /api/v1/campaigns/{campaign_id}/recipients/export
related:
  - campaigns/unsubscribe-and-suppression-list
---

After a campaign starts, you can see how it is doing, overall and for each person.

> **Who can do this:** Admins.

## The campaigns list

**Campaigns** shows one row per campaign with its state, segment, how many were **Sent**, how many **Opened** and how many **Unsubscribed**, when it was created and by whom. Use **All states** to show only drafts, scheduled, sending, sent or other campaigns.

## What each person's status means

| Status | Meaning |
|--------|---------|
| Queued | Waiting to be sent. |
| Sending | CeDesk is sending it now. |
| Sent | The mail server accepted it. |
| Failed | It could not be sent after retries. |
| Bounced | The address does not exist or refused it. |
| Skipped | The person was suppressed or erased after the campaign was queued. |
| Cancelled | The campaign was cancelled before it reached them. |

You can also list only the people who opened, unsubscribed or reported the email as spam.

## Opens are a rough guide

If **Track opens** is on, CeDesk adds a tiny invisible image and counts when it loads. Some mail apps, such as Apple Mail, load images on their own, so the number can be too high. Other people block images, so it can also be too low. Leave it off unless you need the number.

## Export the list

You can download the recipient list as a CSV file. The download is recorded in the audit log.

## Bounces and spam reports

A hard bounce or a spam report that comes back about a campaign email puts the address on the suppression list and makes no ticket. See [Unsubscribe and the suppression list](unsubscribe-and-suppression-list.md).

## Check it worked

Open a sent campaign. The counts match the number of people it went to, less any skipped.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Opened is 0 | Track opens was off, or images are blocked. | Nothing, unless you need the number next time. |
| Many Bounced | The segment has old addresses. | Clean the segment before the next campaign. |
| Many Skipped | People unsubscribed after it was queued. | Expected. |

---
title: Send or schedule a campaign
slug: send-or-schedule-a-campaign
topic: campaigns
order: 4
summary: Send now or at a time, pause and cancel, and what happens to customers suppressed meanwhile.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - POST /api/v1/campaigns/{campaign_id}/send
  - POST /api/v1/campaigns/{campaign_id}/pause
  - POST /api/v1/campaigns/{campaign_id}/resume
  - POST /api/v1/campaigns/{campaign_id}/cancel
related:
  - campaigns/write-and-test-a-campaign
---

When the draft is ready, send it now or pick a time. You are asked to confirm first, because a sent email cannot be taken back.

> **Who can do this:** Admins.

## Send or schedule

1. Open the draft and select **Send**.
2. Choose now, or a later time. A later time must include your time zone and be at most 90 days ahead.
3. Confirm "Send to N people?".

CeDesk copies the people in the segment at the moment sending starts. Customers who join the segment later do not get this campaign.

## How fast it goes

Emails go out in batches, at most 60 a minute for your workspace, and each person gets it once. A big campaign therefore takes a while. A temporary mail error is retried up to three times.

## Pause, resume or cancel

| You want to | Do this | Result |
|-------------|---------|--------|
| Stop a scheduled campaign | Cancel | It goes back to Draft. Nothing was sent. |
| Stop one that is sending | Pause, or Cancel | Paused can be resumed. Cancelled cannot, and already sent emails stay sent. |
| Carry on | Resume | Sending continues with those not yet sent. |

CeDesk pauses a campaign by itself when more than 5 in 100 of the emails sent have bounced, once at least 50 have gone out. An Admin then resumes or cancels it. A high bounce rate usually means the segment has old or wrong addresses.

## People who unsubscribe meanwhile

If someone is added to the suppression list after the campaign was queued, their email is skipped and shown as skipped in the results. See [Unsubscribe and the suppression list](unsubscribe-and-suppression-list.md).

## Check it worked

The campaign state changes to Sending, then Sent, and the Sent number on the Campaigns list grows.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Send is not available | Something is missing. | Read "Before you can send" on the campaign. |
| "Over the daily limit" | Too many campaign emails in 24 hours. | Wait, or send a smaller campaign. See [Campaign limits and compliance](campaign-limits-and-compliance.md). |
| "This campaign would go to N people; the most is 5000" | The segment is too big. | Narrow the segment. |
| Campaign is Failed | Its segment or address was deleted. | Make a new campaign. |

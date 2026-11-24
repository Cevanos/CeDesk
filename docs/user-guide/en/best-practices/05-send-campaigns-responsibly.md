---
title: Send campaigns responsibly
slug: send-campaigns-responsibly
topic: best-practices
order: 5
summary: How to keep your sender reputation, and your customers' trust, when you email many people at once.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes: []
related:
  - campaigns/campaign-limits-and-compliance
  - customers/segments
---

One bad campaign can send your normal support email to spam. These habits prevent that.

> **Who can do this:** Admins. CeDesk is not legal advice. The law where your customers live decides whom you may email.

## Email only people you may email

Send to people who expect to hear from you. See [Campaign limits and compliance](../campaigns/campaign-limits-and-compliance.md).

## Make a tight segment

A small, relevant [segment](../customers/segments.md) does better than everyone. Clean old addresses first. Many bounces pause a campaign by itself.

## Get ready first

Add your postal address and make sure SPF and DKIM pass. See [Get ready to send campaigns](../campaigns/get-ready-to-send-campaigns.md) and [Check email forwarding and DNS](../channels/check-email-forwarding-and-dns.md).

## Test before you send

Send a test to yourself and read it on a phone. See [Write and test a campaign](../campaigns/write-and-test-a-campaign.md).

## Send in a sensible way

Schedule at a time your customers will read it. Remember the daily and per-minute limits. See [Send or schedule a campaign](../campaigns/send-or-schedule-a-campaign.md).

## Respect opt-outs

Never remove someone from the [suppression list](../campaigns/unsubscribe-and-suppression-list.md) unless you have their consent and a written reason.

## Learn from the results

Read [results](../campaigns/campaign-results-and-tracking.md). Treat open numbers as a rough guide, and look at unsubscribes and bounces.

## Check it worked

Bounces and unsubscribes stay low, and your ordinary support email keeps arriving in inboxes.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A campaign paused itself | Too many bounces. | Clean the segment, then resume or cancel. |
| Replies land in spam | DNS is not right, or the content looks like spam. | Re-check DNS, and simplify the email. |

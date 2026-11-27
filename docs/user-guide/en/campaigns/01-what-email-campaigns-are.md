---
title: What email campaigns are
slug: what-email-campaigns-are
topic: campaigns
order: 1
summary: What a campaign is, who can send one, and what the server needs before the first send.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/campaigns
  - POST /api/v1/campaigns
related:
  - campaigns/get-ready-to-send-campaigns
---

A campaign is one email that you write once and send to every customer in a segment. Use it for news, product updates or a notice that applies to many people. It is not for replying to one customer: replies go through tickets.

> **Who can do this:** Admins only. Agents, Supervisors and custom roles cannot see or send campaigns, and an API key cannot either.

## How it works

1. You make a **segment**: a saved filter of customers, for example everyone who ordered in the last month.
2. You write the campaign: a subject and a body, and pick which support address it comes from.
3. You send a test to yourself, then send the campaign now or schedule it.
4. CeDesk copies the people in the segment at the moment sending starts, and sends the email to each of them in small batches.
5. Every email ends with a footer that shows who sent it, your postal address and an unsubscribe link.

## The life of a campaign

| State | What it means |
|-------|---------------|
| Draft | You are still writing it. Only a draft can be edited or deleted. |
| Scheduled | It will start at the time you chose. Cancelling it turns it back into a draft. |
| Sending | CeDesk is sending it. You can pause or cancel it. |
| Paused | Sending stopped. See [bounces](unsubscribe-and-suppression-list.md). An Admin resumes it or cancels it. |
| Sent | Every recipient has been handled. |
| Cancelled | You cancelled it after sending had started. Emails already sent stay sent. |
| Failed | It could not start, for example its segment or address was deleted. |

## What a campaign does not do

- **It does not reach everyone.** People on the suppression list, erased customers, chat customers without a real email address and mail-system addresses such as postmaster are left out.
- **It does not replace ticket email.** Ticket replies and other transactional email are never blocked by the suppression list.

## Check it worked

Open **Campaigns**. Your campaign is in the list with its state, segment and the number sent.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| There is no Campaigns page | Your role is not Admin. | Ask an Admin. |
| Send is not available | Something is missing. The campaign shows a "Before you can send" list. | See [Get ready to send campaigns](get-ready-to-send-campaigns.md). |

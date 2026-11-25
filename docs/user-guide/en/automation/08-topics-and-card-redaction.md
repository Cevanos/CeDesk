---
title: Topics and card redaction
slug: topics-and-card-redaction
topic: automation
order: 8
summary: Give tickets a topic from keywords, and hide card numbers customers send.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/triage-settings
  - PUT /api/v1/triage-settings
related:
  - automation/rules
---

Triage gives each new ticket a topic. Agents see it as a chip and can filter by it.

> **Who can do this:** Admins.

## Topics

Go to **Settings › Service › Topics & redaction**. There are four topics, each with a list of keywords: Refund / return, Billing, Delivery and App problem. A ticket gets a topic when its subject or first message contains one of the words. With none of the words, it is a "General question".

Edit the words to match how your customers write, including other languages. **Reset to built-in lists** goes back to the defaults. New words apply from the next customer message, and a ticket's topic is worked out again when its customer writes again.

## Card redaction

Turn on **Hide card numbers in new messages** and a payment card number in a customer message is masked before the message is saved, for example "●●●● ●●●● ●●●● 4242". The original is not kept, so it cannot be recovered. Messages already saved are not changed, and agents can no longer read a card number a customer sends. Select **Save changes**.

## Check it worked

A test message with the word "refund" gets the Refund / return topic. A test message with a card number shows it masked.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A ticket has the wrong topic | A keyword matches by accident. | Remove or narrow the word. |
| A card number is still visible | The message was saved before redaction was on. | Nothing can be undone; it applies from now on. |

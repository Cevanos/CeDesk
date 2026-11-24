---
title: The web form
slug: web-form
topic: channels
order: 5
summary: A public "Contact us" page where anyone can open a ticket without signing in.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/public/{workspace}/form
  - POST /api/v1/public/{workspace}/form
related:
  - channels/customer-portal
---

The web form is a "Contact us" page. A visitor types their name, email and message, and CeDesk makes a ticket. Your replies reach them by email.

> **Who can do this:** Admins turn it on. Anyone can use it.

## Turn it on

1. Go to **Settings › Customers › Portal**.
2. Under **Features**, turn on **Web form**.
3. Select **Save changes**.

The address of the form is under **Public links**. Share it or link to it from your site.

## What happens

- The visitor gives their name, email and message, and can choose a ticket type that asks for its own fields.
- The ticket gets the channel Web. Your rules, routing and response-time targets run as for any new ticket.
- The first 60 characters of the message become the subject.
- The visitor is not shown a ticket number, because anyone can post to the form.

## Limits

To stop spam, each IP address can send only a few forms, and each email address at most 5 an hour. The form also hides a trap field that people never see. A form filled in by a robot is thrown away.

## Check it worked

Open the form, send a test message with your own email. A ticket appears in **Tickets** with the channel Web.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The form page is not found | **Web form** is off. | Turn it on. |
| The form is refused | The email is wrong, the message is empty, or a field is missing. | Correct it. |
| Too many forms | The limit was reached. | Wait an hour. |

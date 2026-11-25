---
title: The customer portal
slug: customer-portal
topic: channels
order: 4
summary: Let customers sign in with an emailed code to open tickets, follow them, reply and rate the help.
status: draft
updated: 2026-11-24
who: Admins set it up; customers use it
plan: All workspaces
routes:
  - GET /api/v1/portal-settings
  - PUT /api/v1/portal-settings
  - POST /api/v1/portal/{workspace}/code
  - POST /api/v1/portal/{workspace}/session
  - GET /api/v1/portal/tickets
  - POST /api/v1/portal/tickets
related:
  - channels/web-form
---

The portal is a page where your customers see their own tickets, add to them and open new ones. They do not need an account or a password.

> **Who can do this:** Admins set it up under **Settings › Customers › Portal**. Customers use it with no agent login.

## Share the link

**Settings › Customers › Portal** shows your public links under **Public links**: the **Customer portal**, the **Web form** and the **Help center**. Send the portal link to customers or put it on your site.

## What customers do

1. They open the portal and type their email.
2. CeDesk emails a 6-digit code that works for 10 minutes. A new code replaces the last one, and they can ask for 5 an hour.
3. They type the code to sign in.
4. They see their tickets, open a new one by describing the problem and choosing a type, and reply on a ticket.

A reply on a resolved ticket reopens it, unless it is only a thank-you. Customers can see replies and files on their own tickets, but never private notes. They can open up to 10 new tickets an hour.

CeDesk answers the same way whether or not it knows the email, so nobody can use the portal to find out who your customers are.

## Limit who can sign in (Admins)

Under **Who can sign in**, add email domains such as acme.com to allow only people from those domains. Leave it empty to let anyone sign in. When you save a list, customers already signed in with other emails are signed out at once.

## Turn features on or off

Under **Features**, switch **CSAT survey** and **Web form** on or off, then select **Save changes**.

## Check it worked

Sign in on the portal with your own email. The ticket you open there appears in **Tickets** with the channel Portal.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| No code arrives | The email is wrong, or the server cannot send email. | Check spam, try again, and check Failed events. |
| The code does not work | It is wrong, used or expired. After 5 wrong tries it stops working. | Ask for a new code. |
| "Limited to other email domains" | Your domain list does not include this email. | Add the domain. |
| Too many attempts | Too many tries from one address. | Wait and try again. |

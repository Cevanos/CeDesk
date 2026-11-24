---
title: Check email forwarding and DNS
slug: check-email-forwarding-and-dns
topic: channels
order: 2
summary: Prove customer email reaches CeDesk, and that your domain is set up so your replies are not marked as spam.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - POST /api/v1/email-addresses/{address_id}/forwarding-check
  - POST /api/v1/email-addresses/{address_id}/dns-check
related:
  - channels/support-email-addresses
---

Two checks make sure email works both ways. **Forwarding** proves that mail sent to your support address arrives in CeDesk. **DNS records** prove your domain lets CeDesk send as you.

> **Who can do this:** Admins. Whoever runs your server sets how mail arrives.

## Check forwarding

1. Go to **Settings › Channels › Email** and open **Deliverability** under the address.
2. Under **Forwarding**, read where to forward your address. Your mail provider needs a rule that forwards the support address to the address shown after "Forward … to".
3. Select **Send check**.

CeDesk emails the address a one-time code from itself, to itself. When it comes back in, Forwarding turns verified. The check email never makes a ticket. A check works for 7 days. You can send another a minute after the last one.

## Check the DNS records

1. In **Deliverability**, under **DNS records**, select **Check DNS**.
2. Read each result:

| Record | What it does |
|--------|--------------|
| SPF | Says which servers may send for your domain. |
| DKIM | Signs your email so it can be trusted. It needs the **DKIM selector** your mail provider gave you. Type it and select **Save**. |
| DMARC | Says what to do with mail that fails. |

Each shows ok, missing, wrong or not checked, with a note on what to fix. If DNS did not answer, try again. It can take a few seconds.

Campaign email needs both SPF and DKIM to pass. See "Get ready to send campaigns" in Email campaigns.

## For whoever runs the server

How mail reaches CeDesk is set up on the server. Either CeDesk reads a mailbox, or your mail service posts each email to CeDesk. The settings reference lists the settings for both.

## Check it worked

Under **Deliverability**, Forwarding says it is verified and each DNS record says ok.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Forwarding stays "Not checked yet" | The check email did not come back. | Check the forwarding rule at your mail provider and send again. |
| The check email failed to send | The mail service refused it, for example your domain is not verified there. | Verify the domain with your mail service. |
| A second check is refused | The last one was sent under a minute ago. | Wait a minute. |
| DKIM is "wrong" | No selector, or the record is missing. | Type the selector from your mail provider and add its DKIM record. |

---
title: Write and test a campaign
slug: write-and-test-a-campaign
topic: campaigns
order: 3
summary: Subject, Markdown body, the segment, and a test email to yourself before anyone else.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - POST /api/v1/campaigns
  - PATCH /api/v1/campaigns/{campaign_id}
  - GET /api/v1/campaigns/{campaign_id}/audience
  - POST /api/v1/campaigns/{campaign_id}/preview
  - POST /api/v1/campaigns/{campaign_id}/test
related:
  - campaigns/get-ready-to-send-campaigns
---

Write the campaign as a draft, look at it as a customer would, and send a test to yourself. Nothing reaches a customer until you send it.

> **Who can do this:** Admins.

## Write the campaign

1. Go to **Campaigns** and select **New campaign**.
2. **Name** is only for you. Customers never see it. Use up to 120 characters.
3. Pick the **Segment** and the **From** address.
4. Type the **Subject** and the **Body**.
5. Leave **Track opens** off unless you need it. See [Campaign results and tracking](campaign-results-and-tracking.md).

### Placeholders

Put these in the subject or body to fill in each person's details: {{customer.first_name}}, {{customer.name}}, {{agent.name}} and {{fields.<key>}} for a custom field. A customer's own value cannot add links or markup to your email.

### Formatting

The body understands paragraphs, `#` headings, `-` and `1.` lists, **bold**, *italics* and [links](https://example.com). Raw HTML is shown as plain text.

## See who it will reach

The **Recipients** box shows, for example, "Will go to 2 customers. 3 in the segment; 1 left out; 0 on the suppression list." Check it before you send. People are left out when they have no real email address, use a support or mail-system address, are suppressed, or were erased.

## Preview and test

- **Preview** shows the email filled in for one customer.
- **Send test** emails it to you only, with "[Test]" in front of the subject. You can send up to 20 tests an hour.

## Check it worked

The test arrives in your inbox with the footer, your postal address and an unsubscribe link at the bottom.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "Will go to 0 customers" | The segment is empty, or everyone in it is left out. | Check the segment, or the suppression list. |
| The test does not arrive | The sender is not verified, or it is in spam. | See [Get ready to send campaigns](get-ready-to-send-campaigns.md). |
| The body shows raw tags | Raw HTML is not allowed. | Use the formatting above. |

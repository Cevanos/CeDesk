---
title: Unsubscribe and the suppression list
slug: unsubscribe-and-suppression-list
topic: campaigns
order: 5
summary: How unsubscribes, bounces and spam reports keep people off future campaigns.
status: draft
updated: 2026-11-24
who: Anyone who gets a campaign can unsubscribe. Keeping the list is for Admins.
plan: All workspaces
routes:
  - GET /api/v1/suppressions
  - POST /api/v1/suppressions
  - POST /api/v1/suppressions/{suppression_id}/remove
related: []
---

Every campaign email has an unsubscribe link. A person who unsubscribes goes on the suppression list, and no campaign from your workspace goes to them again. You can also add addresses yourself.

> **Who can do this:** Admins.

## How it works

- **The link in the footer** opens the unsubscribe page. Opening the page changes nothing, because mail scanners open links. The person presses the button.
- **The mail app's button** (one-click unsubscribe) unsubscribes at once, without a page.
- An unsubscribe or spam report replaces an earlier hand-added or bounced entry, so the person's own request is what counts.

## Add an address yourself

1. Go to **Campaigns › Suppression list**.
2. Select **Add address**, type the email and an optional note.
3. Select **Save**. The list shows it as *Added by hand*.

> **Note:** Adding or removing an address is recorded in the audit log.

## Can an entry be removed?

| Reason | Removed? |
|--------|----------|
| Added by hand, Bounced | Yes |
| Unsubscribed, Spam complaint | Only if the person asked to get campaigns again. An Admin confirms that and writes a justification |
| Erased customer | Never |

## Check it worked

Look up the address on the list. Its next campaign shows it as *skipped*.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "That address is already on the suppression list." | It is on the list. | Nothing to do. |
| Remove is refused | The person unsubscribed or complained. | Only if they asked to get campaigns again: confirm it and write a justification. |

---
title: Segments
slug: segments
topic: customers
order: 6
summary: Save a filter of customers so you can open it again, and send campaigns to it.
status: draft
updated: 2026-11-24
who: Admins and Supervisors
plan: All workspaces
routes:
  - GET /api/v1/segments
  - POST /api/v1/segments
  - PATCH /api/v1/segments/{segment_id}
  - DELETE /api/v1/segments/{segment_id}
  - GET /api/v1/segments/{segment_id}/contacts
related:
  - customers/find-customers
---

A segment is a saved filter of customers, for example VIPs with an open ticket. Everyone in the workspace can open one from **Contacts**. A campaign is sent to a segment.

> **Who can do this:** Admins and Supervisors save, change and delete segments. Everyone else can open them.

## Save a segment

1. On **Contacts**, set the search and filters you want.
2. Select **Save as segment**.
3. Name it.

It appears under **SEGMENTS** at the top of the list. Select **All contacts** to go back to everyone.

## Change or delete one

Open the actions menu next to the segment and choose **Rename**, **Update filters** or **Delete**.

## Good to know

- A segment is worked out when you open it, so it always shows who matches now. Erased customers are never in it.
- You can keep up to 200 segments, each with its own name.
- Customers must have a real email address to receive a campaign. See [What email campaigns are](../campaigns/what-email-campaigns-are.md).

## Check it worked

The segment is listed under **SEGMENTS**, and selecting it shows the matching customers.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The name is taken | Another segment has it. | Choose another name. |
| **Save as segment** is missing | No filter is on, or your role cannot save segments. | Apply a filter, or ask an Admin or Supervisor. |
| The segment is smaller than before | Customers no longer match, or were erased. | Expected. |

---
title: Merge duplicate customers
slug: merge-duplicate-customers
topic: customers
order: 5
summary: Combine two profiles for the same person into one, after a preview.
status: draft
updated: 2026-11-24
who: Roles with the right to merge customers
plan: All workspaces
routes:
  - POST /api/v1/contacts/{contact_id}/merge-preview
  - POST /api/v1/contacts/{contact_id}/merge
related:
  - customers/the-customer-page
---

The same person sometimes writes from two email addresses and gets two profiles. Merging puts their history in one place.

> **Who can do this:** A role with the right to merge customers. **A merge cannot be undone.**

## Merge

1. Open the profile you want to keep. This is the survivor.
2. Select **More actions**, then **Merge a duplicate into this customer**.
3. Choose the duplicate.
4. Read the preview. It changes nothing. It shows what moves, what the survivor takes, and any conflicts.
5. Confirm.

## What happens

- The survivor keeps its own name and email.
- The duplicate's tickets, archived tickets, ratings, notes and email addresses move to the survivor.
- Details the survivor has no value for are taken from the duplicate. Where both have a value, the survivor's is kept.
- The duplicate's portal sign-ins end, and the duplicate profile is deleted.

## What stops a merge

A merge is refused if either customer was erased, or if the duplicate has a privacy request. The preview tells you why.

## Check it worked

The survivor's page shows the duplicate's tickets, and searching the old email finds the survivor.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| **Merge a duplicate into this customer** is missing | Your role cannot merge customers. | Ask an Admin. |
| The preview says it cannot merge | An erased customer or a privacy request is involved. | Settle the privacy request first. |

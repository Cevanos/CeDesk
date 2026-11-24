---
title: Paging
slug: paging
topic: developers
order: 7
summary: How list routes return pages, and how to read them all.
status: draft
updated: 2026-11-24
who: Developers
plan: All workspaces
routes:
  - GET /api/v1/tickets
  - GET /api/v1/contacts
related:
  - developers/quickstart
---

List routes return a page and a cursor, not page numbers. A cursor stays correct while new records arrive.

> **Who can do this:** A developer.

## Read a list

1. Call the route, for example `GET /tickets?limit=50`. `limit` is how many you want in a page.
2. The answer has your items and `next_cursor`.
3. If `next_cursor` is not empty, call the same route again with `cursor=<next_cursor>` and **the same filters and sort**.
4. Stop when `next_cursor` is empty.

```
cursor=""
while true; do
  page=$(curl -s "$CEDESK_URL/api/v1/tickets?limit=50&cursor=$cursor" -H "Authorization: Bearer $CEDESK_KEY")
  # handle the items in $page
  cursor=$(echo "$page" | jq -r '.next_cursor // empty')
  [ -z "$cursor" ] && break
done
```

## Rules

- A cursor is opaque. Do not build one or read it.
- Change a filter or sort and you must start over without a cursor.
- A search with `q` on some routes returns the best matches only, and no cursor.

## Check it worked

You read every record once, and the last page has no `next_cursor`.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| 422 with a cursor | The filters changed, or the cursor is damaged. | Start again without it. |
| Missing records | You changed the filters between pages. | Keep them the same. |

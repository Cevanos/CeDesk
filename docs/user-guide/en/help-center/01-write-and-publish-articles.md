---
title: Write and publish articles
slug: write-and-publish-articles
topic: help-center
order: 1
summary: Write a help article as a draft, preview it, publish it and take it down again.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/articles
  - POST /api/v1/articles
  - PATCH /api/v1/articles/{article_id}
  - DELETE /api/v1/articles/{article_id}
related:
  - help-center/categories
---

A help center article answers a common question so customers can help themselves before they write to you. Only published articles are public. Drafts stay private.

> **Who can do this:** Admins.

## Write an article

1. Go to **Settings › Customers › Help center** and select **New article**.
2. Type the **Title**.
3. Choose a **Category**, or "No category".
4. Choose the **Status**: Draft or Published.
5. Write the **Article (Markdown)**. Use `#` for headings, `**bold**`, `-` for lists and `[text](https://…)` for links. HTML is not allowed.
6. Check the **Preview** beside it.
7. Select **Save draft**, or save it published.

## Publish and unpublish

Each article in the list shows its status, its category, when it was updated and its votes. Select **Publish** to put a draft in the public help center, **Unpublish** to take it out, or **Edit** to change it. Use the status filter above the list to show drafts or published articles.

## Delete

Deleting an article also deletes its votes.

## Tips

- Write one question per article, and put the question in the title.
- Add a link to the next step, such as your contact form.
- Keep the title short enough to scan in search results.

## Check it worked

Publish the article, then open your help center link in a private window. The article is there. See [The public help center](the-public-help-center.md).

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The article is not public | It is still a draft. | Select **Publish**. |
| The formatting looks wrong | HTML does not work. | Use Markdown only. |
| The page is missing | Your role is not Admin. | Ask an Admin. |

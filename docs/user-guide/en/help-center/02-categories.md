---
title: Categories
slug: categories
topic: help-center
order: 2
summary: Group articles into categories and set the order customers see.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/article-categories
  - POST /api/v1/article-categories
  - PATCH /api/v1/article-categories/{category_id}
  - DELETE /api/v1/article-categories/{category_id}
related:
  - help-center/write-and-publish-articles
---

Categories group your articles, such as Orders or Billing, so customers can browse.

> **Who can do this:** Admins.

## Add a category

1. Go to **Settings › Customers › Help center**.
2. Next to **Categories**, select **Add** and name it.

The list shows each category with how many articles it has. **All articles** shows every article, and **Uncategorized** shows those with no category.

## Rename, reorder and delete

Use **Rename** next to a category. A lower position shows first. **Delete** removes the category, and its articles stay, with no category. Two categories cannot share a name.

## Put an article in a category

Choose a **Category** when you write or edit the article.

## Check it worked

The category shows in the list with the right number of articles, and in the public help center once it has a published article.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The name is taken | Another category has it. | Choose another name. |
| A category is missing publicly | It has no published articles. | Publish one. |
| Articles lost their category | The category was deleted. | Put them in another. |

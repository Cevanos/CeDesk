---
title: The public help center
slug: the-public-help-center
topic: help-center
order: 3
summary: Where customers find your articles, how they search and how they say an article helped.
status: draft
updated: 2026-11-24
who: Customers use it; Admins share the link
plan: All workspaces
routes:
  - GET /api/v1/public/{workspace}/articles
  - GET /api/v1/public/{workspace}/articles/{article_id}
  - POST /api/v1/public/{workspace}/articles/{article_id}/vote
related:
  - help-center/article-feedback
---

The public help center is a page anyone can open without signing in. It shows your published articles.

> **Who can do this:** Anyone with the link. Admins decide what is published.

## Find your link

Go to **Settings › Customers › Portal**. Under **Public links**, copy the **Help center** link. Put it on your website, in your email signature and on your contact form.

## What visitors see

- Your categories, with the number of published articles in each.
- The articles, which they can search.
- Each article with its title, last update and text.
- At the end of an article, "Was this helpful?" with yes and no.

Search shows the best 20 matches. Each visitor has one vote per article, and a new vote replaces the old one. Too many requests from one place are slowed down, to stop robots.

## Check it worked

Open the link in a private window. Your published articles show, and drafts do not.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The page says it is not found | The workspace address is wrong. | Copy the link again from **Portal**. |
| An article is not found | It is a draft or deleted. | Publish it. |
| Too many requests | One place made too many. | Wait a moment. |

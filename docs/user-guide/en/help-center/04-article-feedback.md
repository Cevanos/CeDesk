---
title: See which articles help
slug: article-feedback
topic: help-center
order: 4
summary: Read the helpful and not helpful votes, and improve the articles that miss.
status: draft
updated: 2026-11-24
who: Admins and Supervisors
plan: All workspaces
routes:
  - GET /api/v1/reports/articles
  - GET /api/v1/articles
related:
  - help-center/write-and-publish-articles
---

Visitors answer "Was this helpful?" at the end of an article. The votes tell you which articles work.

> **Who can do this:** Admins read votes in the article list. Supervisors and Admins read them in Reports.

## In the article list

**Settings › Customers › Help center** shows, beside each article, its helpful votes, its not helpful votes and the percentage that were helpful. A dash means no votes yet.

## In Reports

The **Team** report has a **Help articles** table for the period you choose, with each article's helpful and not helpful votes. **Download CSV** saves it. Each visitor has one vote per article, and their latest answer counts.

## What to do with it

- An article with many "not helpful" votes needs a rewrite, or the answer is not what people expect.
- A question customers still ask in tickets, though an article exists, may need a better title.
- Link popular articles from your saved replies.

## Check it worked

After a visitor votes, the count changes in the list.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| All votes are zero | Nobody has voted, or the article is new. | Wait, or share the link. |
| The Help articles table is empty | No votes in the period. | Choose a longer period. |

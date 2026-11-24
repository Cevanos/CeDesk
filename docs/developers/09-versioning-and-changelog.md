---
title: Versioning and the changelog
slug: versioning-and-changelog
topic: developers
order: 9
summary: What stays the same, what can be added, and where changes are announced.
status: draft
updated: 2026-11-24
who: Developers
plan: All workspaces
routes:
  - GET /api/v1/me
related:
  - developers/errors-and-limits
---

You can build on the CeDesk API without fearing a surprise. This page says what we promise.

> **Who can do this:** A developer.

## The promise

- Every route is under `/api/v1`.
- **Breaking changes only come in a new version, v2.** Inside v1, a route keeps its path, its meaning and the fields it returns.
- New routes, new optional request fields, new fields in a response and new values in a list such as an event kind can appear in v1.

## Write a tolerant client

- Ignore fields you do not know.
- Do not break on a new value in an enum. Handle it as "other".
- Use the `code` of an error, not the `message`, to decide what to do. Messages can be reworded.

## The changelog

Every change to the API is in `docs/api/CHANGELOG.md`, newest first, with the date and the routes it touches. Read it when you upgrade, or subscribe to the repository. A few common entries:

- new routes, for example replaying a rule on past tickets,
- new fields and limits,
- a corrected description.

## The API description

`docs/api/openapi.json` is the API as served, in the OpenAPI format. Generate clients from it. It is also what builds the API reference.

## Health checks

`GET /healthz` says the process is up. `GET /readyz` says the database answers. Neither needs sign-in. Use them for your load balancer, not for monitoring your integration.

## Check it worked

Your client keeps working after you upgrade, and the changelog explains any new field you see.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A response has a field you did not expect | It was added. | Ignore it. |
| A route you used is gone | It was never in the changelog as removed. | Report it as a bug. |

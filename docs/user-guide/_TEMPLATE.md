---
title: <Task in plain words, e.g. "Send or schedule a campaign">
slug: <kebab-case>
topic: <topic slug>
order: <number inside the topic>
summary: <One sentence for the topic page and search results.>
status: draft            # draft | published (published needs real screenshots)
updated: <YYYY-MM-DD>
who: <Admins | Supervisors and Admins | Agents | Anyone>
plan: <All workspaces | name the plan or limit>
routes:                  # checked against docs/api/openapi.json in CI
  - POST /api/v1/<route>
settings:                # checked against docs/SETTINGS.md in CI (self-hosted only)
  - <SETTING_NAME>
related:
  - <topic>/<slug>
---

<Intro: two sentences. What this is, and what the reader will be able to do.>

> **Who can do this:** <role>. **Plan:** <plan>.

## How it works

<Short explanation or bullets. Define a term once and link to the glossary.>

## <Do the task, e.g. "Add an address yourself">

1. Go to **Settings › <Area> › <Page>**.
2. Select **<Button>**, then <one action>.
3. Select **Save**.

![<What the screenshot shows>](../../_img/<slug>-1.png)

> **Note:** <Only for something the reader would otherwise get wrong.>

## <Optional reference table, e.g. "Can an entry be removed?">

| Item | Result |
|------|--------|
| <label on screen> | <outcome> |

## Check it worked

<One observable result the reader can see in the product.>

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "<exact message>" | <cause> | <action> |

## Related

- [<Article title>](../<topic>/<slug>.md)

# Request: serve the docs inside the app at /docs

For the front-end developer. About half a day. No API change.

## What and why

We wrote a user guide, developer guides and an API reference (about 114 articles and 346
API routes). They are built into plain HTML files. We want them on the **same website as the
app**: `http://156.67.82.131:6543/docs/`. The files need no server code, so the front end
serves them from its `public` folder, and links to them from the app.

## How it renders (read this first)

- The docs are **finished HTML pages**, not app components. The build already turned every
  article into a normal `.html` file with its own styles.
- The front end does **not render** them. It copies the files into `public/docs/` and Next.js
  serves them as they are, like any static website. The browser shows them directly.
- The pages fetch no data, call no API and run no scripts. Links between pages are plain
  relative links.
- What a person sees: **Help** or **Learn more** opens `/docs/...` and replaces the app screen
  with a docs page that has its own purple header ("CeDesk Docs"), the topics on the left and
  the article. It has **no app sidebar or top bar**, so it looks like a separate site on the
  same address. Framing it inside the app is not possible with the current security headers
  (`frame-src 'none'`, `X-Frame-Options: DENY`).

## 1. Get the files

Two ways:

- **From GitHub:** Actions › the latest **Docs site** run on `main` › Artifacts › `docs-site`.
  Unzip it.
- **Build it yourself** (needs Python 3.12 and `uv`):

  ```
  DOCS_BASE_PATH=/docs PYTHONPATH=scripts uv run python scripts/build_docs.py
  ```

  The result is the folder `docs/_site/` in the CDESK repository.

You should see `index.html`, `404.html`, `glossary.html`, one folder per topic and an `api/`
folder. Every link between pages is relative and ends in `.html`.

## 2. Put them in the front-end project

1. Copy the contents of that folder to **`public/docs/`**, so that
   `public/docs/index.html` exists.
2. Make `/docs` and `/docs/` open `/docs/index.html`. In `next.config`:

   ```js
   async rewrites() {
     return [
       { source: "/docs", destination: "/docs/index.html" },
       { source: "/docs/", destination: "/docs/index.html" },
     ];
   }
   ```

   (Today `/docs/` answers a redirect to `/docs`, and `/docs` answers a redirect to
   `/login?next=%2Fdocs`, because the sign-in redirect covers every unknown path.)
3. **Decide who may read it:**
   - *Signed-in people only* (fine for now, every article is still a draft): nothing more
     to do if static files in `public` already pass your sign-in check. Check it.
   - *Everyone:* add `/docs` to the paths your sign-in redirect lets through (your
     middleware's allow list or matcher).

Keep the copy step in the front end's build, or commit the files. Re-copy when the docs
change (every merge to `main` makes a new artifact).

## 3. Links in the app

- A **Help** item in the account menu (and the top bar, if you like) that opens `/docs/`.
- A **Learn more** link on each page, pointing at that page's main article:
  `/docs/<topic>/<slug>.html`. The map is below. Example: Settings › SLA →
  `/docs/automation/sla-targets-and-policies.html`.

Open the links in a new tab so people do not lose their place.

### Page-to-article map

Where a page has several articles, this is the main one.

| App page | Link (after `/docs/`) |
|----------|------------------|
| Tickets | `working-with-tickets/find-and-sort-tickets.html` |
| New ticket | `working-with-tickets/create-a-ticket.html` |
| A ticket | `working-with-tickets/reply-and-add-private-notes.html` |
| Contacts | `customers/find-customers.html` |
| A customer | `customers/the-customer-page.html` |
| Agents | `automation/routing-and-agent-availability.html` |
| Archive | `ticket-tools/find-archived-tickets.html` |
| Campaigns | `campaigns/what-email-campaigns-are.html` |
| Suppression list | `campaigns/unsubscribe-and-suppression-list.html` |
| Reports (all pages) | `reports/reports-overview.html` |
| Settings › Members | `account-and-access/add-and-manage-members.html` |
| Settings › Teams | `account-and-access/teams.html` |
| Settings › Roles | `account-and-access/roles-and-rights.html` |
| Settings › Ticket types | `ticket-tools/ticket-types-and-fields.html` |
| Settings › SLA | `automation/sla-targets-and-policies.html` |
| Settings › Rules | `automation/rules.html` |
| Settings › Workflows | `automation/workflows.html` |
| Settings › Auto-close | `automation/auto-close.html` |
| Settings › Macros | `working-with-tickets/macros.html` |
| Settings › Saved replies | `working-with-tickets/saved-replies.html` |
| Settings › Topics and redaction | `automation/topics-and-card-redaction.html` |
| Settings › Contact fields | `customers/contact-fields.html` |
| Settings › Portal | `channels/customer-portal.html` |
| Settings › Email | `channels/support-email-addresses.html` |
| Settings › Cevanos | `integrations/connect-cevanos.html` |
| Settings › Failed events | `channels/retry-failed-emails-and-events.html` |
| Settings › Imports | `imports/plan-your-move.html` |
| Settings › Apps | `integrations/install-apps.html` |
| Settings › API keys | `account-and-access/api-keys.html` |
| Settings › Webhooks | `integrations/webhooks.html` |
| Settings › Security | `security-and-privacy/security-settings.html` |
| Settings › Privacy | `security-and-privacy/export-a-customers-data.html` |
| Settings › Audit log | `security-and-privacy/audit-log.html` |
| Settings › Plan and retention | `getting-started/your-plan-and-usage.html` |
| Settings › Sandbox | `security-and-privacy/sandbox.html` |
| Settings › Demo data | `getting-started/try-cedesk-with-demo-data.html` |
| My account | `account-and-access/sign-in-and-passwords.html` |

## 4. Check it works

1. Open `/docs/` while signed in: the docs home page shows with a purple header.
2. Click a topic, then an article, then **API reference**. No page should redirect to
   `/login`, and no link should be broken.
3. Open `/docs/campaigns/send-or-schedule-a-campaign.html` directly.
4. Open `/docs/nothing.html`. You should get a not-found page (the docs' own `404.html` is
   in the folder; wire it up if your server can).
5. Click **Help** and a **Learn more** link in the app.

## Notes

- **The search box is not in this build.** Your security header only allows scripts with a
  per-request nonce, and static files cannot have one. The pages work without scripts. If
  you want search later: exempt `/docs` from that header, then we rebuild with search on.
- **Every article is a draft** and shows a "Draft" badge. The pages ask search engines not
  to index them. That changes when a topic is reviewed and published; nothing for you to do.
- **No customer data** is in these files, and they call no API.
- This was built and checked locally only. It has not been tried inside the real app. If
  something here does not match your project, tell us what and we will adjust the build.

Questions about the docs build: the person who owns `scripts/build_docs.py`. Details and
the reasons for these choices: `docs/user-guide/HOSTING.md`.

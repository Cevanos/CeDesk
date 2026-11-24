# Hosting the docs: plan

Status: proposal, waiting for three decisions (see the end). Nothing here is built yet,
except the site itself: `uv run python scripts/build_docs.py` writes plain static files to
`docs/_site/` (user guide, developer guides and API reference, 15 topics).

## 1. What we are hosting

One static site, no server code and no database. About 110 pages plus 49 API pages. It
changes only when a pull request changes the Markdown or `docs/api/openapi.json`.

## 2. Name and address

Do not call it "Help Center". The product already has a feature called **Help center**
(Settings › Customers › Help center): the articles a workspace shows its own customers at
`/<workspace>/help`. Two things with one name will confuse agents and customers.

- Name: **CeDesk Docs**.
- Address: a subdomain of the main site, for example `docs.<your domain>`. A subdomain
  keeps the app's cookies and the docs apart, and needs no change to the app's proxy.
- The site header and page titles say "CeDesk Help Center" today. Rename them when the
  name is decided (one string in `scripts/build_docs.py`).

## 3. Where to host (pick one)

| Option | Good | Watch out for |
|--------|------|---------------|
| **A. Cloudflare Pages** (recommended) | Free, works with a private repository, CDN, previews for every pull request, custom domain with HTTPS, no server to look after. | Needs the domain's DNS at Cloudflare, or a CNAME to them. |
| B. Our own server (Caddy) | Same machine and same tooling as the app. One more site block in `deploy/Caddyfile` that serves a folder of files. | We look after it. The certificate and disk are ours. A bad deploy can take docs down with the app. |
| C. GitHub Pages | Free, built from the repository. | Private repositories need a paid plan. No pull request previews. |

Recommendation: **A**. The docs must stay up when the app is down, because that is when
people read them. Hosting them on the app's own server (B) puts both behind one failure.

## 4. Build and deploy

1. **CI (already there):** every pull request runs the docs check and builds the site, so
   a broken link, a route that no longer exists or a bad spec fails the PR.
2. **Preview:** each pull request publishes a preview address (Option A does this itself).
3. **Deploy:** a merge to `main` runs `scripts/build_docs.py` and uploads `docs/_site/`.
4. **Two sites, not one:**
   - **Staging** (`docs-staging.<domain>`, not indexed by search engines, behind a company
     login): everything, drafts included, with the "Draft" badge. Used by the team to
     review.
   - **Public** (`docs.<domain>`): only articles marked `status: published`. Today every
     article is a draft, so the public site stays empty until someone reviews a topic and
     flips its articles. This needs a small change to the build: a `--public` flag that
     leaves out drafts.
5. **Publishing a topic** means: close its items in `REVIEW.md`, change `status: draft` to
   `published`, merge. The public site updates in a minute.

## 5. Link the docs from the app

The app's front end is a separate project (`docs/frontend/FRONTEND_HANDOFF_CEDESK.md`). It
needs two small changes, and the docs team supplies the links:

- A **Help** item in the account menu and the top bar, opening the docs home.
- A **Learn more** link on each page that points at that page's main article. The links
  use the article address: `<docs address>/<topic>/<slug>.html`. A first map:

| App page | Article |
|----------|---------|
| Tickets | `working-with-tickets/find-and-sort-tickets` |
| New ticket | `working-with-tickets/create-a-ticket` |
| A ticket | `working-with-tickets/reply-and-add-private-notes` |
| Contacts | `customers/find-customers` |
| A customer | `customers/the-customer-page` |
| Agents | `automation/routing-and-agent-availability` |
| Archive | `ticket-tools/find-archived-tickets` |
| Campaigns | `campaigns/what-email-campaigns-are` |
| Suppression list | `campaigns/unsubscribe-and-suppression-list` |
| Reports (all pages) | `reports/reports-overview`, then the page's own article |
| Settings › Members | `account-and-access/add-and-manage-members` |
| Settings › Teams | `account-and-access/teams` |
| Settings › Roles | `account-and-access/roles-and-rights` |
| Settings › Ticket types | `ticket-tools/ticket-types-and-fields` |
| Settings › SLA | `automation/sla-targets-and-policies` |
| Settings › Rules | `automation/rules` |
| Settings › Workflows | `automation/workflows` |
| Settings › Auto-close | `automation/auto-close` |
| Settings › Macros | `working-with-tickets/macros` |
| Settings › Saved replies | `working-with-tickets/saved-replies` |
| Settings › Topics and redaction | `automation/topics-and-card-redaction` |
| Settings › Contact fields | `customers/contact-fields` |
| Settings › Portal | `channels/customer-portal` |
| Settings › Email | `channels/support-email-addresses` |
| Settings › Cevanos | `integrations/connect-cevanos` |
| Settings › Failed events | `channels/retry-failed-emails-and-events` |
| Settings › Imports | `imports/plan-your-move` |
| Settings › Apps | `integrations/install-apps` |
| Settings › API keys | `account-and-access/api-keys` |
| Settings › Webhooks | `integrations/webhooks` |
| Settings › Security | `security-and-privacy/security-settings` |
| Settings › Privacy | `security-and-privacy/export-a-customers-data` |
| Settings › Audit log | `security-and-privacy/audit-log` |
| Settings › Plan and retention | `getting-started/your-plan-and-usage` |
| Settings › Sandbox | `security-and-privacy/sandbox` |
| Settings › Demo data | `getting-started/try-cedesk-with-demo-data` |
| My account | `account-and-access/sign-in-and-passwords` |

Rule: **never change a published article's address.** If a slug must change, keep a
redirect from the old one. A check in CI can compare slugs against the last deploy.

## 6. Search

The built site has no search yet. Add **Pagefind** (a static search index built after the
site, no server, about 100 KB): one extra step in the deploy. It indexes only what the
public site contains.

## 7. Self-hosted customers

Customers who run CeDesk on their own server may have no internet access to our docs.
Publish the built site as a zip with each release (`docs-<version>.zip`), and let the API
image optionally serve it at `/docs` when a folder is mounted.

## 8. Extras to switch on at launch

- `sitemap.xml` and `robots.txt` (public site only; staging says "noindex").
- A real 404 page.
- Basic privacy-friendly page-view counts, no cookies.
- Security headers on the static host. The site needs no scripts, so a strict content
  security policy is easy.

## 9. Order of work

1. The decisions below.
2. Rename the site and add the `--public` flag (small).
3. Cloudflare Pages project, staging site, pull request previews.
4. Help link and Learn more links in the app (front end team).
5. Review one topic end to end, publish it, launch the public site with that topic.
6. Search, sitemap and the rest of the extras.
7. Publish the other topics as each is reviewed.

## Decisions needed

1. **The address**: domain and subdomain.
2. **The host**: A (recommended), B or C.
3. **The name**: CeDesk Docs, or another.

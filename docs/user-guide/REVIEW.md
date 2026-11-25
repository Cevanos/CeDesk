# Review status of the user guide

Every article is `status: draft`. This file lists what was checked and what a person still
has to confirm before an article is marked `published`. Update it as items are closed.

## What has been checked

- **Automatically (CI):** front matter, slugs, every listed route exists in `openapi.json`,
  related articles and links resolve, no raw internal values in the user guide, and the
  whole site builds.
- **Labels (24 Nov 2026):** the bold labels used in the articles were compared with the
  live app's 46 screens. Every label that can be read without opening a dialog, menu or
  tab was found. Labels inside dialogs and menus were read when each article was written.
- **Developer guides:** both webhook signature samples were run against the real signer.

## Not yet confirmed (needs a person with a test workspace)

| Topic | What to confirm |
|-------|-----------------|
| Getting started | The sign-up screens and field labels (needs a server with sign-up on); the minimum password length on sign-up. |
| Working with tickets | The Accept button on a chat handover; how a bulk change is confirmed; notification wording. |
| Ticket tools | The forms inside Tasks, Time, Approvals, Side conversations and Jira; where waiting approvals and "my tasks" are shown. |
| Channels | The customer side of the portal, web form and survey email; the web form's per-IP limit; how mail reaches the server (mailbox or webhook). |
| Customers | The CSV import dialog; the merge preview screen; which roles may merge. |
| Automation | The rule and workflow editors' labels; where Replay is on screen; how one agent's own capacity and skills are set. |
| Reports | The forms for a custom report, a dashboard and a schedule; the AI page; how "SLA met" is calculated. |
| Security and privacy | The erase confirmation step; what Verify chain reports; how the sandbox is created and deployed. |
| Account and access | Two-step setup screens and recovery codes; the Make API key form; the sign-in page. |
| Integrations | The install form of each app; the webhook add form and delivery log; whether the Shopify and Stripe cards show (they are Unavailable while Cevanos is connected). |
| Help center | The public help center pages; the Edit article form. |
| Moving to CeDesk | Every connector. `docs/imports/MIGRATION_PROGRESS.md` says none is live-verified. Run a real import from a trial account first. |
| Email campaigns | The Send screen and its confirmation; results after a real send; the suppression reason labels. |
| Developer guides | Run the curl examples and open a live stream against a test server. |

## Things in the test server to clean up before taking screenshots

- About ten revoked "delete me" API keys and a test role "QA roles test (delete me)".
- Twenty help articles, many of them duplicates or drafts.
- Two webhooks that point at a public test site.
- One saved reply uses a placeholder that is not on the supported list
  (`/generic-how-can-i-help`).

## Legal wording to review

The compliance article in Email campaigns, and the export and erase articles in Security and
privacy, describe duties without being legal advice. Have someone qualified read them.

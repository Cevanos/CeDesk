# Moving to CeDesk: go-live and rollback

How to switch from another help desk to CeDesk without a big-bang risk: check before you
import, run both help desks side by side, check again before go-live, and know how to go
back. Who: Admins. All routes are under `/api/v1` and need an Admin.

## 1. Before importing

1. Connect the old help desk (`POST /imports/connections`) or upload a CSV file
   (`POST /imports/csv`).
2. Read the **pre-migration report**: `GET /imports/connections/{id}/report`.
   - `imports`: CeDesk brings it, with the source's count.
   - `needs_setup`: CeDesk doesn't bring it. Set it up first:
     - **Agents**: invite them before importing. They are matched by email; a message from
       an agent who isn't in CeDesk keeps the agent's name and the ticket stays unassigned.
     - **Groups to teams**: create a team for each group the report names, or pick one
       when you start the import.
     - **Triggers, automations, SLA policies, views, reports, email addresses, business
       hours**: rebuild them in CeDesk. Test each rule in test mode before switching it on.
   - `cannot_move`: this source can't give it to CeDesk (for example help articles from
     HubSpot, or who changed what on a ticket). Keep an export from the old help desk if
     you need it.
3. Run a **dry run** (`POST /imports/connections/{id}/dry-run`) with the team mapping you
   will use. It saves nothing.

## 2. Run both side by side

1. Start the import **with sync on**: `POST /imports/connections/{id}/run` with
   `{"sync": true, "team_map": {...}}`. Not possible for a CSV file.
2. The old help desk stays the place where agents work. Every 5 minutes CeDesk brings new
   tickets, new messages, status changes, owners, teams and new tags.
3. Imported and synced tickets **send nothing and run nothing in CeDesk**: no emails to
   customers, no CSAT surveys, no rules, no routing, no notifications, no webhooks. So
   customers never get a reply twice.
4. Meanwhile, let a few agents try CeDesk on the imported tickets, and test your rebuilt
   rules in test mode.

## 3. Before go-live: the cutover checklist

Read `GET /imports/runs/{run_id}/cutover`. Go live only when `ready` is `true`.

| Check | What it means when it's a problem |
|---|---|
| `import_finished` | The import is still running or failed. Wait, or resume it. |
| `counts_match` | Records the source counted aren't in CeDesk. See the run's report. |
| `no_open_failures` | Records couldn't be read. Import again, or dismiss each one. |
| `sync_fresh` | CeDesk hasn't looked at the source in 15 minutes. Use `sync-now`. (Skipped when not syncing.) |
| `agents_matched` | Messages are from agents who aren't in CeDesk. Invite them. |
| `teams_set` | Open imported tickets have no team, so no queue shows them. |
| `email_ready` | No support address has verified forwarding, so new emails won't reach CeDesk. |

## 4. Go-live

1. Point email forwarding (or the MX record) at CeDesk. Send a test email and check it
   becomes a ticket.
2. `POST /imports/runs/{run_id}/sync-now` for a last look, wait for it to finish, then
   `POST /imports/runs/{run_id}/sync-stop`. CeDesk stops asking the old help desk and
   deletes the stored credentials.
3. Switch on your rules and workflows in CeDesk.
4. Tell agents to work only in CeDesk from now on. Keep the old help desk read-only for a
   while.

## 5. Rollback (if go-live goes wrong)

Going back is safe only while the old help desk still has everything.

1. **Point email back** at the old help desk (forwarding or MX record). New customer emails
   go there again.
2. **Switch off rules and workflows in CeDesk**, so nothing is sent from it.
3. **Tickets created or answered in CeDesk after go-live** are not in the old help desk. List
   them (the ticket list, created after the go-live time, or not tagged `imported-<source>`)
   and export or copy them by hand. CeDesk never writes to the old help desk.
4. Fix what went wrong, then start again from step 2 ("Run both side by side"). Running the
   import again creates no duplicates: tickets already here are updated, not copied.

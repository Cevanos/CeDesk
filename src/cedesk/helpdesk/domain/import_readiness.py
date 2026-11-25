"""Before and after a migration (differentiator plan #4): the pre-migration report and the
cutover checklist. Pure rules: no I/O.

- **Pre-migration report**: what an Admin learns before importing anything. Each item is
  `imports` (CeDesk brings it, with the source's count), `needs_setup` (CeDesk doesn't bring
  it: rebuild it, or set it up first) or `cannot_move` (this source can't give it to
  CeDesk). The source's own warnings are listed beside the items, unchanged.
- **Cutover checklist**: what to check before go-live (turning the old help desk off). Each
  check is `ok`, `problem` or `skipped`; the run is ready when no check is a problem.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from cedesk.helpdesk.domain.imports import GROUP_NAMES, KINDS, label

# What each source's connector can read (`page(kind, ...)`); a CSV file holds tickets and
# the customers who wrote them.
SUPPORTED_KINDS: Mapping[str, tuple[str, ...]] = {
    "zendesk": KINDS,
    "freshdesk": KINDS,
    "intercom": KINDS,
    "helpscout": KINDS,
    "gorgias": ("contacts", "tickets", "replies"),
    "hubspot": ("contacts", "tickets"),
    "zoho": ("contacts", "tickets", "articles"),
    "csv": ("contacts", "tickets"),
}

KIND_NAMES: Mapping[str, str] = {
    "contacts": "Customers",
    "tickets": "Tickets",
    "articles": "Help center articles",
    "replies": "Macros (as saved replies)",
}
KIND_NOTES: Mapping[str, str] = {
    "contacts": "With their name, email addresses, phone, organization and custom "
    "attributes, when the source gives them.",
    "tickets": "With their messages, internal notes, files, tags, custom fields, status, "
    "satisfaction ratings and links between tickets. Each gets the tag imported-<source> "
    "and a first line saying where it came from.",
    "articles": "With their categories.",
    "replies": "Each macro's text becomes a saved reply.",
}

GROUPS_SHOWN = 10  # unmatched groups named in the report; the rest are counted

# What CeDesk never imports from any help desk, and where to set it up instead.
SETUP_ITEMS: tuple[tuple[str, str, str], ...] = (
    (
        "agents",
        "Agents",
        "Invite your agents before importing: they are matched by email. A message from an "
        "agent who isn't in CeDesk keeps the agent's name, and the ticket stays unassigned.",
    ),
    (
        "automation",
        "Triggers, automations and SLA policies",
        "Not imported. Rebuild them in CeDesk (Rules, Workflows, SLA policies); test each "
        "rule in test mode before switching it on.",
    ),
    (
        "views",
        "Views and reports",
        "Not imported. Recreate them as saved views and saved reports.",
    ),
    (
        "email",
        "Support email addresses",
        "Not imported. Add each address in Settings > Email and verify its forwarding, so "
        "new customer emails reach CeDesk.",
    ),
    (
        "business_hours",
        "Business hours and holidays",
        "Not imported. Set them in Settings, so SLA times are counted correctly.",
    ),
)
HISTORY_NOTE = (
    "Who changed what on a ticket, and when, isn't copied: each ticket comes with its "
    "current state and its messages."
)


@dataclass(frozen=True)
class ReportItem:
    key: str
    name: str
    status: str  # imports | needs_setup | cannot_move
    count: int | None
    note: str


@dataclass(frozen=True)
class PreMigrationReport:
    source: str
    items: list[ReportItem]
    warnings: list[str]

    def total(self, status: str) -> int:
        return sum(1 for i in self.items if i.status == status)


def pre_migration_report(
    source: str,
    counts: Mapping[str, int],
    group_count: int,
    unmatched_groups: Sequence[str],
    warnings: Sequence[str],
) -> PreMigrationReport:
    """`counts`: what the source holds per kind (`discover`). `unmatched_groups`: the source
    groups no CeDesk team matches by name."""
    name = label(source)
    supported = SUPPORTED_KINDS.get(source, ())
    items: list[ReportItem] = []
    for kind in KINDS:
        if kind in supported:
            items.append(
                ReportItem(kind, KIND_NAMES[kind], "imports", int(counts.get(kind, 0)),
                           KIND_NOTES[kind])
            )  # fmt: skip
        else:
            items.append(
                ReportItem(kind, KIND_NAMES[kind], "cannot_move", None,
                           f"CeDesk can't read {KIND_NAMES[kind].lower()} from {name}.")
            )  # fmt: skip
    groups = GROUP_NAMES.get(source, "Groups")
    if unmatched_groups:
        shown = ", ".join(unmatched_groups[:GROUPS_SHOWN])
        more = len(unmatched_groups) - GROUPS_SHOWN
        tail = f" (and {more} more)" if more > 0 else ""
        items.append(
            ReportItem(
                "groups", f"{groups} to teams", "needs_setup", len(unmatched_groups),
                f"No CeDesk team matches: {shown}{tail}. Create the teams, or pick a team "
                "for each when you start the import; tickets left without one have no team.",
            )
        )  # fmt: skip
    else:
        items.append(
            ReportItem("groups", f"{groups} to teams", "imports", group_count,
                       "Each one matches a CeDesk team by name.")
        )  # fmt: skip
    items += [ReportItem(k, n, "needs_setup", None, note) for k, n, note in SETUP_ITEMS]
    items.append(ReportItem("history", "Ticket change history", "cannot_move", None, HISTORY_NOTE))
    return PreMigrationReport(source, items, list(warnings))


# Cutover checklist

SYNC_FRESH = timedelta(minutes=15)  # three missed looks (one every 5 minutes) is stale


@dataclass(frozen=True)
class Check:
    key: str
    status: str  # ok | problem | skipped
    detail: str


@dataclass(frozen=True)
class CutoverFacts:
    source: str
    state: str  # the run's: queued | running | done | failed
    sync_on: bool
    last_sync_at: datetime | None
    open_failures: int
    missing: int  # records the source counted that the run didn't account for
    unknown_agent_messages: int
    open_without_team: int  # open imported tickets with no team
    verified_addresses: int  # support addresses with forwarding verified


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def cutover_checks(f: CutoverFacts, now: datetime) -> list[Check]:
    name = label(f.source)
    out: list[Check] = []
    if f.state == "done":
        out.append(Check("import_finished", "ok", "The import finished."))
    elif f.state == "failed":
        out.append(Check("import_finished", "problem", "The import failed. Resume it first."))
    else:
        out.append(Check("import_finished", "problem", "The import is still running."))

    if f.missing:
        out.append(
            Check("counts_match", "problem",
                  f"{_plural(f.missing, 'record', 'records')} counted in {name} "
                  "aren't in CeDesk. See the run's report.")
        )  # fmt: skip
    else:
        out.append(Check("counts_match", "ok", f"Everything {name} counted is accounted for."))

    if f.open_failures:
        out.append(
            Check("no_open_failures", "problem",
                  f"{_plural(f.open_failures, 'record', 'records')} couldn't be read. Import "
                  "again, or dismiss each one in the run's failures.")
        )  # fmt: skip
    else:
        out.append(Check("no_open_failures", "ok", "No record is waiting to be read again."))

    if f.source == "csv":
        out.append(Check("sync_fresh", "skipped", "A CSV file can't keep syncing."))
    elif not f.sync_on:
        out.append(
            Check("sync_fresh", "skipped",
                  f"Not syncing: anything that changed in {name} after the import isn't in "
                  "CeDesk. Import again just before go-live, or start the import with sync.")
        )  # fmt: skip
    elif f.last_sync_at is None or now - f.last_sync_at > SYNC_FRESH:
        out.append(
            Check("sync_fresh", "problem",
                  f"CeDesk hasn't looked at {name} in the last 15 minutes. Use Sync now.")
        )  # fmt: skip
    else:
        out.append(Check("sync_fresh", "ok", f"CeDesk looked at {name} in the last 15 minutes."))

    if f.unknown_agent_messages:
        out.append(
            Check("agents_matched", "problem",
                  f"{_plural(f.unknown_agent_messages, 'message is', 'messages are')} from "
                  f"agents who aren't in CeDesk. Invite them before go-live.")
        )  # fmt: skip
    else:
        out.append(Check("agents_matched", "ok", "Every agent's messages matched a member."))

    if f.open_without_team:
        out.append(
            Check("teams_set", "problem",
                  f"{_plural(f.open_without_team, 'open imported ticket has', 'open imported tickets have')} "  # noqa: E501
                  "no team. Give them a team, or nobody's queue shows them.")
        )  # fmt: skip
    else:
        out.append(Check("teams_set", "ok", "Every open imported ticket has a team."))

    if f.verified_addresses:
        out.append(Check("email_ready", "ok", "A support address has verified forwarding."))
    else:
        out.append(
            Check("email_ready", "problem",
                  "No support address has verified forwarding. Add one in Settings > Email, "
                  "so new customer emails reach CeDesk after go-live.")
        )  # fmt: skip
    return out


def ready(checks: Sequence[Check]) -> bool:
    return all(c.status != "problem" for c in checks)

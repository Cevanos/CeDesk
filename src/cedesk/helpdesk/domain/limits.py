"""Cloud plan limits, usage and retention (md O2, O4, O5, D2, D6), the rules that need no
database.

- Limits apply only on our cloud (`CEDESK_EDITION=cloud`) and only to a workspace that has
  been given a plan (`cedesk set-plan`). A self-hosted install, or a workspace with no
  plan, has no limits (md D2: we charge only for what we provide).
- The plans' numbers come from config (`CEDESK_PLANS`, JSON); these are the proposal in md
  D6 until Amol sets them: Free 3 agents, 1,000 tickets a month, 1 GB, 1 year of history;
  Growth 50 GB, 3 years; Enterprise no limits.
- What counts: agents = active members who take tickets (light agents are free seats,
  md A2); tickets = made this calendar month (UTC), not imported; storage = the files'
  sizes. Over a limit: 402 with a clear message, when adding a member, uploading a file,
  or an agent or API key opening a ticket. A customer's email, chat or portal request is
  never refused: losing it would be worse.
- Retention: the Admin's "delete resolved tickets after N days", capped by the plan's
  history; the worker deletes them (and their files) once a day and audits each run.
"""

import json
from dataclasses import dataclass
from enum import StrEnum

GB = 1024**3


class Kind(StrEnum):
    agents = "agents"
    tickets_month = "tickets_month"
    storage_bytes = "storage_bytes"


@dataclass(frozen=True)
class Plan:
    name: str
    agents: int | None = None
    tickets_month: int | None = None
    storage_bytes: int | None = None
    history_days: int | None = None

    def limit(self, kind: Kind) -> int | None:
        value: int | None = getattr(self, kind.value)
        return value


DEFAULT_PLANS: dict[str, Plan] = {
    "free": Plan("free", agents=3, tickets_month=1000, storage_bytes=1 * GB, history_days=365),
    "growth": Plan("growth", storage_bytes=50 * GB, history_days=3 * 365),
    "enterprise": Plan("enterprise"),
}
MIN_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 3650
RETENTION_BATCH = 500
KEEP_EXPORT_DAYS = 30
"""A retention export (O2: the tickets retention deleted, written first when the Admin
asks) is kept this long for the Admin to download, then deleted."""


class BadPlans(ValueError):
    pass


def parse_plans(raw: str) -> dict[str, Plan]:
    """`CEDESK_PLANS`: {"free": {"agents": 3, "tickets_month": 1000, "storage_gb": 1,
    "history_days": 365}, ...}. Empty: the defaults. A missing number is no limit."""
    if not raw.strip():
        return dict(DEFAULT_PLANS)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise BadPlans("CEDESK_PLANS isn't JSON.") from exc
    if not isinstance(data, dict) or not data:
        raise BadPlans("CEDESK_PLANS is an object of plans.")
    plans = {}
    for name, spec in data.items():
        if not isinstance(spec, dict):
            raise BadPlans(f"Plan {name!r} needs an object.")
        gb = spec.get("storage_gb")
        plans[str(name)] = Plan(
            str(name),
            agents=_count(spec.get("agents")),
            tickets_month=_count(spec.get("tickets_month")),
            storage_bytes=int(float(gb) * GB) if gb is not None else None,
            history_days=_count(spec.get("history_days")),
        )
    return plans


def _count(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value < 0:
        raise BadPlans(f"{value!r} isn't a count.")
    return value


_WHAT = {
    Kind.agents: "agents",
    Kind.tickets_month: "tickets this month",
    Kind.storage_bytes: "storage",
}


def _show(kind: Kind, value: int) -> str:
    return f"{value / GB:.1f} GB" if kind is Kind.storage_bytes else str(value)


def refusal(plan: Plan | None, kind: Kind, used: int, adding: int) -> str | None:
    """Why `adding` more would go over the plan, or None when it fits (or no plan)."""
    limit = plan.limit(kind) if plan is not None else None
    if plan is None or limit is None or used + adding <= limit:
        return None
    return (
        f"The {plan.name.capitalize()} plan allows {_show(kind, limit)} {_WHAT[kind]}; this "
        f"workspace has {_show(kind, used)}. Upgrade the plan, or free some up."
    )


def retention_days(setting: int | None, plan: Plan | None) -> int | None:
    """Days after resolving that a ticket is deleted: the Admin's setting, capped by the
    plan's history; None keeps tickets for good."""
    cap = plan.history_days if plan is not None else None
    choices = [d for d in (setting, cap) if d is not None]
    return min(choices) if choices else None

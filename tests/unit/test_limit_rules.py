"""Plan limits and retention rules (md O2, O4, D6), no database."""

import pytest

from cedesk.helpdesk.domain import limits as rules
from cedesk.helpdesk.domain.limits import GB, Kind, Plan


def test_the_default_plans_are_the_md_proposal() -> None:
    free = rules.DEFAULT_PLANS["free"]
    assert (free.agents, free.tickets_month, free.storage_bytes, free.history_days) == (
        3, 1000, GB, 365)  # fmt: skip
    assert rules.DEFAULT_PLANS["growth"].storage_bytes == 50 * GB
    assert rules.DEFAULT_PLANS["enterprise"] == Plan("enterprise")


def test_plans_from_config() -> None:
    plans = rules.parse_plans('{"free": {"agents": 5, "storage_gb": 0.5}, "pro": {}}')
    assert plans["free"] == Plan("free", agents=5, storage_bytes=GB // 2)
    assert plans["pro"] == Plan("pro")
    assert rules.parse_plans("  ") == rules.DEFAULT_PLANS
    for bad in (
        "not json",
        "[]",
        "{}",
        '{"x": 1}',
        '{"x": {"agents": -1}}',
        '{"x": {"agents": "3"}}',
    ):
        with pytest.raises(rules.BadPlans):
            rules.parse_plans(bad)


def test_what_goes_over() -> None:
    free = rules.DEFAULT_PLANS["free"]
    assert rules.refusal(free, Kind.agents, 2, 1) is None  # 3 of 3
    reason = rules.refusal(free, Kind.agents, 3, 1)
    assert reason is not None and reason.startswith("The Free plan allows 3 agents")
    assert rules.refusal(free, Kind.agents, 3, 0) is None  # already at it: fine
    assert rules.refusal(free, Kind.agents, 4, 0) is not None  # over it after a change
    storage = rules.refusal(free, Kind.storage_bytes, GB, 1)
    assert storage is not None and "1.0 GB storage" in storage
    assert rules.refusal(None, Kind.agents, 99, 1) is None  # no plan
    assert rules.refusal(rules.DEFAULT_PLANS["growth"], Kind.agents, 99, 1) is None  # no limit


def test_retention_days() -> None:
    plan = Plan("p", history_days=365)
    assert rules.retention_days(None, None) is None
    assert rules.retention_days(90, None) == 90
    assert rules.retention_days(None, plan) == 365
    assert rules.retention_days(90, plan) == 90
    assert rules.retention_days(1000, plan) == 365  # the plan caps it

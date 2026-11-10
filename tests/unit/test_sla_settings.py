"""The SLA settings helpers around `domain/sla.py` (md W9)."""

import pytest

from cedesk.helpdesk.domain.sla import DEFAULT_TARGETS, Target
from cedesk.helpdesk.domain.sla_settings import (
    MAX_MINUTES,
    duration,
    reads_as,
    target_problem,
    with_defaults,
)


@pytest.mark.parametrize(
    ("minutes", "text"),
    [(15, "15 min"), (60, "1 h"), (90, "1 h 30 min"), (240, "4 h"), (4320, "72 h")],
)
def test_duration(minutes: int, text: str) -> None:
    assert duration(minutes) == text


def test_reads_as() -> None:
    assert reads_as("high", Target(60, 480)) == (
        "High tickets get a first reply within 1 h and are resolved within 8 h of business time."
    )


def test_target_problems() -> None:
    assert target_problem(15, 240) is None
    assert target_problem(240, 240) is None
    assert target_problem(241, 240) == "The first reply can't be due after the resolution."
    assert target_problem(0, 240) is not None
    assert target_problem(15, MAX_MINUTES + 1) is not None


def test_defaults_fill_what_was_not_saved() -> None:
    mine = Target(5, 60)
    merged = with_defaults({"urgent": mine})
    assert list(merged) == ["urgent", "high", "medium", "low"]
    assert merged["urgent"] == mine and merged["low"] == DEFAULT_TARGETS["low"]

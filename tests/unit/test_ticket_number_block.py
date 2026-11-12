"""`reserve_numbers` (imports reserve a batch's ticket numbers): a count below 1 is refused
before anything is sent to the database, because zero would hand a number out twice and a
negative count would move the ticket counter backwards."""

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from cedesk.helpdesk.repo import tickets as ticket_repo


class _NoDatabase:
    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the database must not be touched for a bad count")


@pytest.mark.parametrize("count", [0, -1, -100])
def test_a_count_below_one_is_refused_without_touching_the_counter(count: int) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        asyncio.run(ticket_repo.reserve_numbers(_NoDatabase(), uuid4(), count))  # type: ignore[arg-type]

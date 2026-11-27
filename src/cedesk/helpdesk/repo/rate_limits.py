"""SQL for limits shared by every API process (`rate_limit_hit`, a global table)."""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def hit(
    session: AsyncSession, bucket: str, key_hash: bytes, window_seconds: int
) -> tuple[int, datetime, datetime]:
    """Counts one request in the current fixed window: (hits so far, window start, now)."""
    result = await session.execute(
        text(
            "INSERT INTO rate_limit_hit (bucket, key_hash, window_start, hits) "
            "VALUES (:bucket, :key, to_timestamp(floor(extract(epoch FROM now()) / :w) * :w), 1) "
            "ON CONFLICT (bucket, key_hash, window_start) "
            "DO UPDATE SET hits = rate_limit_hit.hits + 1 "
            "RETURNING hits, window_start, now() AS now"
        ),
        {"bucket": bucket, "key": key_hash, "w": window_seconds},
    )
    row = result.mappings().one()
    return int(row["hits"]), row["window_start"], row["now"]


async def sweep(session: AsyncSession, before: datetime) -> int:
    result = await session.execute(
        text("DELETE FROM rate_limit_hit WHERE window_start < :before"), {"before": before}
    )
    return int(result.rowcount)  # type: ignore[attr-defined]

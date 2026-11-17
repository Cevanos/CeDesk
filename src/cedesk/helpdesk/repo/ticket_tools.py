"""SQL for the ticket tools: merge, links, child tickets, approvals, tasks, time log.
Always run inside a `workspace_session`."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_BRIEF = (
    "SELECT t.id, t.number, t.subject, t.status, t.channel, t.assignee_id, "
    "a.name AS assignee_name, t.team_id, t.type_id, t.fields, t.contact_id, "
    "c.name AS contact_name, c.email AS contact_email, t.parent_id, t.merged_into_id, t.source, "
    "t.accepted, t.created_at "
    "FROM hd_ticket t JOIN hd_contact c ON c.id = t.contact_id "
    "LEFT JOIN member a ON a.id = t.assignee_id "
)


async def brief(session: AsyncSession, ticket_id: UUID) -> Row | None:
    result = await session.execute(text(_BRIEF + "WHERE t.id = :id"), {"id": ticket_id})
    return result.mappings().first()


async def lock(session: AsyncSession, ticket_ids: Sequence[UUID]) -> dict[UUID, Row]:
    """Locks the tickets in id order (so two merges of the same pair can't deadlock)."""
    found: dict[UUID, Row] = {}
    for ticket_id in sorted(set(ticket_ids)):
        result = await session.execute(
            text(_BRIEF + "WHERE t.id = :id FOR UPDATE OF t"), {"id": ticket_id}
        )
        row = result.mappings().first()
        if row is not None:
            found[ticket_id] = row
    return found


async def open_children(session: AsyncSession, parent_id: UUID) -> list[int]:
    result = await session.execute(
        text(
            "SELECT number FROM hd_ticket WHERE parent_id = :id AND status <> 'resolved' "
            "AND merged_into_id IS NULL ORDER BY number"
        ),
        {"id": parent_id},
    )
    return [int(n) for n in result.scalars().all()]


# --- Merge (X1) ---


async def copy_messages(session: AsyncSession, source_id: UUID, target_id: UUID) -> int:
    """Copies the source's customer, agent, AI and note messages into the target with
    their original times, so they sort in among the target's. Their files move with
    them. System lines stay on the source. Returns how many were copied."""
    result = await session.execute(
        text(
            "SELECT id FROM hd_message WHERE ticket_id = :src AND kind <> 'system' "
            "ORDER BY created_at, id"
        ),
        {"src": source_id},
    )
    copied = 0
    for old_id in result.scalars().all():
        new_id = (
            await session.execute(
                text(
                    "INSERT INTO hd_message (workspace_id, ticket_id, kind, body, "
                    "author_member_id, author_name, email, created_at) "
                    "SELECT workspace_id, :dst, kind, body, author_member_id, author_name, "
                    "email, created_at FROM hd_message WHERE id = :old RETURNING id"
                ),
                {"dst": target_id, "old": old_id},
            )
        ).scalar_one()
        await session.execute(
            text(
                "UPDATE hd_attachment SET ticket_id = :dst, message_id = :new, "
                "updated_at = now() WHERE message_id = :old"
            ),
            {"dst": target_id, "new": new_id, "old": old_id},
        )
        await session.execute(
            text(
                "INSERT INTO hd_mention (workspace_id, message_id, member_id, created_at) "
                "SELECT workspace_id, :new, member_id, created_at FROM hd_mention "
                "WHERE message_id = :old"
            ),
            {"new": new_id, "old": old_id},
        )
        copied += 1
    # Files uploaded to the ticket but never sent on a message move too.
    await session.execute(
        text(
            "UPDATE hd_attachment SET ticket_id = :dst, updated_at = now() "
            "WHERE ticket_id = :src AND message_id IS NULL"
        ),
        {"src": source_id, "dst": target_id},
    )
    return copied


async def mark_merged(session: AsyncSession, source_id: UUID, target_id: UUID) -> None:
    await session.execute(
        text(
            "UPDATE hd_ticket SET merged_into_id = :dst, status = 'resolved', "
            "resolved_at = coalesce(resolved_at, now()), updated_at = now() WHERE id = :src"
        ),
        {"src": source_id, "dst": target_id},
    )


async def touch(session: AsyncSession, ticket_id: UUID) -> None:
    await session.execute(
        text("UPDATE hd_ticket SET updated_at = now() WHERE id = :id"), {"id": ticket_id}
    )


# --- Links (X2) and the Related card ---


async def add_link(
    session: AsyncSession, workspace_id: UUID, a: UUID, b: UUID, by: UUID | None
) -> bool:
    """False when the two were already linked."""
    result = await session.execute(
        text(
            "INSERT INTO hd_link (workspace_id, ticket_a, ticket_b, created_by) "
            "VALUES (:ws, :a, :b, :by) ON CONFLICT (workspace_id, ticket_a, ticket_b, kind) "
            "DO NOTHING RETURNING id"
        ),
        {"ws": workspace_id, "a": a, "b": b, "by": by},
    )
    return result.first() is not None


async def remove_link(session: AsyncSession, a: UUID, b: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_link WHERE ticket_a = :a AND ticket_b = :b RETURNING id"),
        {"a": a, "b": b},
    )
    return result.first() is not None


# A `team_only` role sees only its team's tickets (`:vt`); NULL means every ticket.
_VISIBLE = "(CAST(:vt AS uuid) IS NULL OR t.team_id = CAST(:vt AS uuid))"

_RELATED_COLUMNS = (
    "SELECT t.id, t.number, t.subject, t.status, a.name AS assignee_name, "
    "tm.name AS team_name "
    "FROM hd_ticket t LEFT JOIN member a ON a.id = t.assignee_id "
    "LEFT JOIN hd_team tm ON tm.id = t.team_id "
)


async def related(
    session: AsyncSession, ticket_id: UUID, limit: int, visible_team: UUID | None = None
) -> dict[str, list[Row]]:
    """Each list is newest first and at most `limit` long; with `visible_team`, only that
    team's tickets (a `team_only` role)."""
    queries = {
        "links": "WHERE t.id IN (SELECT CASE WHEN ticket_a = :id THEN ticket_b ELSE ticket_a "
        "END FROM hd_link WHERE ticket_a = :id OR ticket_b = :id)",
        "children": "WHERE t.parent_id = :id",
        "merged_from": "WHERE t.merged_into_id = :id",
    }
    out: dict[str, list[Row]] = {}
    for key, where in queries.items():
        result = await session.execute(
            text(
                _RELATED_COLUMNS
                + where
                + " AND "
                + _VISIBLE
                + " ORDER BY t.number DESC LIMIT :limit"
            ),
            {"id": ticket_id, "limit": limit, "vt": visible_team},
        )
        out[key] = list(result.mappings().all())
    return out


async def related_one(
    session: AsyncSession, ticket_id: UUID | None, visible_team: UUID | None = None
) -> Row | None:
    if ticket_id is None:
        return None
    result = await session.execute(
        text(
            _RELATED_COLUMNS
            + "WHERE t.id = :id AND (CAST(:vt AS uuid) IS NULL OR t.team_id = CAST(:vt AS uuid))"
        ),
        {"id": ticket_id, "vt": visible_team},
    )
    return result.mappings().first()


# --- Approvals (X6) ---

_APPROVAL = (
    "SELECT p.id, p.ticket_id, t.number AS ticket_number, p.what, p.amount, p.currency, "
    "p.state, p.decision_note, p.requester_id, r.name AS requester_name, p.approver_id, "
    "a.name AS approver_name, p.decided_by, d.name AS decided_by_name, p.decided_at, "
    "p.created_at, p.cevanos_hold_id "
    "FROM hd_approval p JOIN hd_ticket t ON t.id = p.ticket_id "
    # A Governance hold (X7) has no requester or approver: anyone who approves decides.
    "LEFT JOIN member r ON r.id = p.requester_id LEFT JOIN member a ON a.id = p.approver_id "
    "LEFT JOIN member d ON d.id = p.decided_by "
)


async def insert_approval(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    ticket_id: UUID,
    requester_id: UUID,
    approver_id: UUID,
    what: str,
    amount: Decimal | None,
    currency: str | None,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_approval (workspace_id, ticket_id, requester_id, approver_id, "
            "what, amount, currency) VALUES (:ws, :t, :r, :a, :what, :amount, :currency) "
            "RETURNING id"
        ),
        {
            "ws": workspace_id,
            "t": ticket_id,
            "r": requester_id,
            "a": approver_id,
            "what": what,
            "amount": amount,
            "currency": currency,
        },
    )
    return UUID(str(result.scalar_one()))


async def insert_hold(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    ticket_id: UUID,
    hold_id: str,
    what: str,
    amount: Decimal | None,
    currency: str | None,
) -> UUID | None:
    """A Cevanos Governance hold as an approval request (X7); None when it is already one
    (the same handover delivered again)."""
    result = await session.execute(
        text(
            "INSERT INTO hd_approval (workspace_id, ticket_id, cevanos_hold_id, what, amount, "
            "currency) VALUES (:ws, :t, :hold, :what, :amount, :currency) "
            "ON CONFLICT (workspace_id, cevanos_hold_id) WHERE cevanos_hold_id IS NOT NULL "
            "DO NOTHING RETURNING id"
        ),
        {
            "ws": workspace_id,
            "t": ticket_id,
            "hold": hold_id,
            "what": what,
            "amount": amount,
            "currency": currency,
        },
    )
    found = result.scalar()
    return UUID(str(found)) if found is not None else None


async def get_approval(
    session: AsyncSession, approval_id: UUID, *, lock: bool = False
) -> Row | None:
    sql = _APPROVAL + "WHERE p.id = :id" + (" FOR UPDATE OF p" if lock else "")
    result = await session.execute(text(sql), {"id": approval_id})
    return result.mappings().first()


async def ticket_approvals(session: AsyncSession, ticket_id: UUID) -> Sequence[Row]:
    result = await session.execute(
        text(_APPROVAL + "WHERE p.ticket_id = :id ORDER BY p.created_at DESC, p.id DESC"),
        {"id": ticket_id},
    )
    return result.mappings().all()


async def waiting_for(
    session: AsyncSession,
    approver_id: UUID,
    limit: int,
    *,
    holds: bool = False,
    team: UUID | None = None,
) -> Sequence[Row]:
    """Asked of this member; with `holds`, also the Governance holds nobody decided yet
    (only that team's tickets, for a team-only role)."""
    result = await session.execute(
        text(
            _APPROVAL + "WHERE p.state = 'pending' AND (p.approver_id = :m "
            "OR (CAST(:holds AS boolean) AND p.cevanos_hold_id IS NOT NULL "
            "    AND (CAST(:team AS uuid) IS NULL OR t.team_id = CAST(:team AS uuid)))) "
            "ORDER BY p.created_at, p.id LIMIT :limit"
        ),
        {"m": approver_id, "limit": limit, "holds": holds, "team": team},
    )
    return result.mappings().all()


async def decide(
    session: AsyncSession, approval_id: UUID, *, state: str, note: str | None, by: UUID
) -> None:
    await session.execute(
        text(
            "UPDATE hd_approval SET state = :state, decision_note = :note, decided_by = :by, "
            "decided_at = now() WHERE id = :id"
        ),
        {"id": approval_id, "state": state, "note": note, "by": by},
    )


# --- Tasks (X8) ---

_TASK = (
    "SELECT k.id, k.ticket_id, t.number AS ticket_number, k.text, k.owner_id, "
    "o.name AS owner_name, k.due_at, k.done_at, k.done_by, d.name AS done_by_name, "
    "k.created_by, k.created_at "
    "FROM hd_task k JOIN hd_ticket t ON t.id = k.ticket_id "
    "JOIN member o ON o.id = k.owner_id LEFT JOIN member d ON d.id = k.done_by "
)


async def insert_task(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    ticket_id: UUID,
    owner_id: UUID,
    text_: str,
    due_at: datetime | None,
    by: UUID,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_task (workspace_id, ticket_id, owner_id, text, due_at, created_by, "
            "alerted_due) VALUES (:ws, :t, :o, :text, CAST(:due AS timestamptz), :by, "
            "CASE WHEN CAST(:due AS timestamptz) <= now() THEN CAST(:due AS timestamptz) END) "
            "RETURNING id"
        ),
        {"ws": workspace_id, "t": ticket_id, "o": owner_id, "text": text_, "due": due_at, "by": by},
    )
    return UUID(str(result.scalar_one()))


async def get_task(session: AsyncSession, task_id: UUID, *, lock: bool = False) -> Row | None:
    sql = _TASK + "WHERE k.id = :id" + (" FOR UPDATE OF k" if lock else "")
    result = await session.execute(text(sql), {"id": task_id})
    return result.mappings().first()


async def ticket_tasks(session: AsyncSession, ticket_id: UUID) -> Sequence[Row]:
    result = await session.execute(
        text(_TASK + "WHERE k.ticket_id = :id ORDER BY k.done_at NULLS FIRST, k.created_at, k.id"),
        {"id": ticket_id},
    )
    return result.mappings().all()


async def open_tasks_of(session: AsyncSession, owner_id: UUID, limit: int) -> Sequence[Row]:
    """The owner's open tasks, the ones due soonest first (no due time last)."""
    result = await session.execute(
        text(
            _TASK + "WHERE k.owner_id = :m AND k.done_at IS NULL "
            "ORDER BY k.due_at NULLS LAST, k.created_at, k.id LIMIT :limit"
        ),
        {"m": owner_id, "limit": limit},
    )
    return result.mappings().all()


async def update_task(
    session: AsyncSession,
    task_id: UUID,
    *,
    text_: str,
    owner_id: UUID,
    due_at: datetime | None,
    done_at: datetime | None,
    done_by: UUID | None,
) -> None:
    await session.execute(
        text(
            "UPDATE hd_task SET text = :text, owner_id = :o, "
            "alerted_due = CASE WHEN due_at IS NOT DISTINCT FROM CAST(:due AS timestamptz) "
            "THEN alerted_due WHEN CAST(:due AS timestamptz) <= now() "
            "THEN CAST(:due AS timestamptz) END, "
            "due_at = CAST(:due AS timestamptz), done_at = :done_at, done_by = :done_by "
            "WHERE id = :id"
        ),
        {
            "id": task_id,
            "text": text_,
            "o": owner_id,
            "due": due_at,
            "done_at": done_at,
            "done_by": done_by,
        },
    )


async def overdue_tasks(session: AsyncSession, now: datetime, limit: int) -> Sequence[Row]:
    """Open tasks past their due time whose owner hasn't been told about that due time.
    Locked (SKIP LOCKED), so two workers never alert the same task."""
    result = await session.execute(
        text(
            "SELECT k.id, k.ticket_id, t.number AS ticket_number, k.text, k.owner_id, "
            "o.is_active AS owner_active FROM hd_task k JOIN hd_ticket t ON t.id = k.ticket_id "
            "JOIN member o ON o.id = k.owner_id "
            "WHERE k.done_at IS NULL AND k.due_at <= :now "
            "AND k.alerted_due IS DISTINCT FROM k.due_at "
            "ORDER BY k.due_at LIMIT :limit FOR UPDATE OF k SKIP LOCKED"
        ),
        {"now": now, "limit": limit},
    )
    return result.mappings().all()


async def mark_tasks_alerted(session: AsyncSession, task_ids: Sequence[UUID]) -> None:
    await session.execute(
        text("UPDATE hd_task SET alerted_due = due_at WHERE id = ANY(:ids)"),
        {"ids": list(task_ids)},
    )


async def move_open_work(
    session: AsyncSession, source_id: UUID, target_id: UUID
) -> tuple[int, int]:
    """On a merge: the source's open tasks and waiting approvals go to the target.
    Done tasks and decided approvals stay as history. Returns (tasks, approvals)."""
    tasks = await session.execute(
        text(
            "UPDATE hd_task SET ticket_id = :dst WHERE ticket_id = :src AND done_at IS NULL "
            "RETURNING id"
        ),
        {"src": source_id, "dst": target_id},
    )
    approvals = await session.execute(
        text(
            "UPDATE hd_approval SET ticket_id = :dst WHERE ticket_id = :src "
            "AND state = 'pending' RETURNING id"
        ),
        {"src": source_id, "dst": target_id},
    )
    return len(tasks.all()), len(approvals.all())


# --- Time log (X9) ---

_TIME = (
    "SELECT l.id, l.ticket_id, l.member_id, m.name AS member_name, l.minutes, l.note, "
    "l.billable, l.logged_at FROM hd_time_log l LEFT JOIN member m ON m.id = l.member_id "
)


async def insert_time(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    ticket_id: UUID,
    member_id: UUID,
    minutes: int,
    note: str | None,
    billable: bool,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_time_log (workspace_id, ticket_id, member_id, minutes, note, "
            "billable) VALUES (:ws, :t, :m, :min, :note, :billable) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "t": ticket_id,
            "m": member_id,
            "min": minutes,
            "note": note,
            "billable": billable,
        },
    )
    return UUID(str(result.scalar_one()))


async def get_time(session: AsyncSession, entry_id: UUID) -> Row | None:
    result = await session.execute(text(_TIME + "WHERE l.id = :id"), {"id": entry_id})
    return result.mappings().first()


async def ticket_time(session: AsyncSession, ticket_id: UUID) -> Sequence[Row]:
    result = await session.execute(
        text(_TIME + "WHERE l.ticket_id = :id ORDER BY l.logged_at DESC, l.id DESC"),
        {"id": ticket_id},
    )
    return result.mappings().all()


async def time_totals(session: AsyncSession, ticket_id: UUID) -> tuple[int, int]:
    result = await session.execute(
        text(
            "SELECT coalesce(sum(minutes), 0), "
            "coalesce(sum(minutes) FILTER (WHERE billable), 0) "
            "FROM hd_time_log WHERE ticket_id = :id"
        ),
        {"id": ticket_id},
    )
    total, billable = result.one()
    return int(total), int(billable)


async def delete_time(session: AsyncSession, entry_id: UUID) -> None:
    await session.execute(text("DELETE FROM hd_time_log WHERE id = :id"), {"id": entry_id})

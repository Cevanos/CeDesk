"""Exports of tickets as JSON (md S11 privacy export, O2 retention export). Pure: rows in,
plain data out, for `json.dumps`."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

Row = Mapping[Any, Any]  # a database row, read by name


@dataclass(frozen=True)
class ExportFile:
    name: str
    data: bytes


def when(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _receipt(r: Row) -> dict[str, Any]:
    """A resolution receipt (differentiator plan #5) in the export."""
    return {
        "request": r["request"],
        "action_taken": r["action_taken"],
        "result": r["result"],
        "verified": r["verified"],
        "verified_at": when(r["verified_at"]),
        "customer_notified": r["customer_notified"],
        "pending_item": r["pending_item"],
        "created_at": when(r["created_at"]),
    }


def export_tickets(
    tickets: Sequence[Row],
    messages: Sequence[Row],
    files: Sequence[Row],
    ratings: Sequence[Row],
    receipts: Sequence[Row] = (),
) -> list[dict[str, Any]]:
    """Live tickets in the export's shape (also the retention export's, md O2)."""
    by_ticket: dict[UUID, list[dict[str, Any]]] = {t["id"]: [] for t in tickets}
    files_by_message: dict[UUID | None, list[dict[str, Any]]] = {}
    for f in files:
        files_by_message.setdefault(f["message_id"], []).append(
            {"name": f["name"], "size": f["size"], "type": f["mime"]}
        )
    for m in messages:
        by_ticket[m["ticket_id"]].append(
            {
                "at": when(m["created_at"]),
                "kind": m["kind"],
                "from": m["member_name"] or m["author_name"],
                "text": m["body"],
                "files": files_by_message.get(m["id"], []),
            }
        )
    rating_of = {r["ticket_id"]: r for r in ratings}
    receipt_of = {r["ticket_id"]: _receipt(r) for r in receipts}
    return [
        {
            "number": t["number"],
            "subject": t["subject"],
            "status": t["status"],
            "priority": t["priority"],
            "channel": t["channel"],
            "type": t["type_name"],
            "fields": t["fields"],
            "tags": list(t["tags"]),
            "created_at": when(t["created_at"]),
            "resolved_at": when(t["resolved_at"]),
            "messages": by_ticket[t["id"]],
            "rating": (
                {
                    "score": rating_of[t["id"]]["score"],
                    "comment": rating_of[t["id"]]["comment"],
                    "rated_at": when(rating_of[t["id"]]["rated_at"]),
                }
                if t["id"] in rating_of
                else None
            ),
            "resolution": receipt_of.get(t["id"]),
        }
        for t in tickets
    ]


def archived_export(a: Row) -> dict[str, Any]:
    """An archived ticket (md O3) in the export's ticket shape, marked `archived`."""
    ticket = a["ticket"]
    files: dict[Any, list[dict[str, Any]]] = {}
    for f in a["files"]:
        files.setdefault(f.get("message_id"), []).append(
            {"name": f.get("name"), "size": f.get("size"), "type": f.get("mime")}
        )
    rating = a["rating"]
    return {
        "number": a["number"],
        "subject": a["subject"],
        "status": a["status"],
        "priority": a["priority"],
        "channel": a["channel"],
        "type": ticket.get("type_name"),
        "fields": ticket.get("fields") or {},
        "tags": list(ticket.get("tags") or []),
        "created_at": when(a["created_at"]),
        "resolved_at": when(a["resolved_at"]),
        "archived": True,
        "messages": [
            {
                "at": m.get("created_at"),
                "kind": m.get("kind"),
                "from": m.get("author_name"),
                "text": m.get("body"),
                "files": files.get(m.get("id"), []),
            }
            for m in a["messages"]
        ],
        "rating": (
            {
                "score": rating.get("score"),
                "comment": rating.get("comment"),
                "rated_at": rating.get("rated_at"),
            }
            if rating
            else None
        ),
    }

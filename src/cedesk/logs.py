"""Structured logs (md 2B): one JSON object per line, with the request, workspace and
ticket the line belongs to, so one ticket's history can be followed across lines.

Ids, route templates, status codes and timings only: never message bodies, search text,
query strings, tokens or customer data. Code adds context with `bind()`; the request
middleware in main.py clears it for each request.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar("cedesk_log_context", default=None)

# Keys a caller may bind or pass as `extra=`; anything else is dropped, so a stray
# `extra={"body": ...}` can never put customer text in the logs.
ALLOWED = frozenset(
    {"request_id", "workspace_id", "member_id", "ticket_id", "method", "route", "status",
     "ms", "event", "sqlstate", "error", "count"}
)  # fmt: skip


def bind(**values: str | UUID | int | None) -> None:
    """Updates the request's dict in place: the route runs in a child task (Starlette's
    call_next), and only a shared object carries its values back to the request line."""
    current = _CONTEXT.get()
    if current is None:
        current = {}
        _CONTEXT.set(current)
    current.update({k: str(v) for k, v in values.items() if v is not None and k in ALLOWED})


def reset() -> None:
    _CONTEXT.set({})


def context() -> dict[str, str]:
    return dict(_CONTEXT.get() or {})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "at": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        line.update(context())
        line.update({k: v for k, v in record.__dict__.items() if k in ALLOWED})
        if record.exc_info and record.exc_info[0] is not None:
            line["error"] = record.exc_info[0].__name__  # the type only, not the message
        return json.dumps(line, default=str)


def configure(level: str = "INFO") -> None:
    """JSON lines on stdout for the `cedesk` loggers and uvicorn's error log. Uvicorn's
    access log is turned off: the request middleware writes a safer line per request."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    for name in ("cedesk", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(level.upper())
        logger.propagate = False
    logging.getLogger("uvicorn.access").disabled = True

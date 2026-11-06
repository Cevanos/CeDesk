"""Placeholders in replies (and later macros): {{customer.first_name}}, {{customer.name}},
{{agent.name}} and {{fields.<key>}}. An empty value becomes "—"; unknown placeholders stay
as written, so a typo is visible instead of silently vanishing."""

import re
from collections.abc import Mapping

EMPTY = "—"
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+(?:\.[a-z0-9_]+)?)\s*\}\}")


def _text(value: object) -> str:
    if value is None or value == "":
        return EMPTY
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render(body: str, *, customer_name: str, agent_name: str, fields: Mapping[str, object]) -> str:
    def value(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == "customer.name":
            return _text(customer_name.strip())
        if key == "customer.first_name":
            parts = customer_name.split()
            return _text(parts[0] if parts else "")
        if key == "agent.name":
            return _text(agent_name.strip())
        if key.startswith("fields."):
            return _text(fields.get(key.removeprefix("fields.")))
        return match.group(0)

    return PLACEHOLDER.sub(value, body)

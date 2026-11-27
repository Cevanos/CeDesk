"""Every MCP tool says what it does to the AI app: the directories reject tools without a
title and hints, and a read tool must never change anything."""

import asyncio

from cedesk.mcp import server, tools
from cedesk.settings import Settings

EXPECTED = {
    # tickets
    "search_tickets": "read", "get_ticket": "read",
    "reply_to_ticket": "write", "update_ticket": "write",
    # reports
    "get_live_report": "read", "get_backlog_report": "read", "get_team_report": "read",
    "get_metrics_report": "read", "run_report": "read",
    # setup
    "list_teams": "read", "list_macros": "read", "list_sla_policies": "read",
    "list_rules": "read", "list_routing": "read",
    "create_team": "write", "create_macro": "write", "update_sla_policy": "write",
    "create_rule": "write", "update_routing": "write",
    # OpenAI company knowledge
    "search": "read", "fetch": "read",
}  # fmt: skip
NEVER = ("member", "role", "security", "sso", "api_key", "cevanos", "import", "privacy",
         "approv", "campaign")  # fmt: skip


def _listed() -> list[dict[str, object]]:
    mcp = server.build(Settings(oauth_issuer="https://t"), lambda: None)  # type: ignore[arg-type,return-value]
    found = asyncio.run(mcp.server.list_tools())
    return [t.model_dump(by_alias=True, exclude_none=True) for t in found]


def test_the_tool_list_is_exactly_the_reviewed_one() -> None:
    assert {t["name"] for t in _listed()} == set(EXPECTED)
    assert {name: spec.scope for name, spec in tools.SPECS.items()} == EXPECTED


def test_every_tool_has_a_title_all_four_hints_and_a_short_name() -> None:
    for tool in _listed():
        name = str(tool["name"])
        assert len(name) <= 64
        assert tool.get("title"), name
        assert tool.get("description"), name
        hints = tool["annotations"]
        assert isinstance(hints, dict)
        for key in ("title", "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
            assert key in hints, (name, key)
        assert hints["readOnlyHint"] is (EXPECTED[name] == "read"), name
        if hints["readOnlyHint"]:
            assert hints["destructiveHint"] is False, name


def test_every_write_tool_takes_an_idempotency_key() -> None:
    for tool in _listed():
        if EXPECTED[str(tool["name"])] == "write":
            schema = tool["inputSchema"]
            assert isinstance(schema, dict)
            assert "idempotency_key" in schema["properties"], tool["name"]


def test_no_tool_reaches_people_access_or_the_cevanos_link() -> None:
    for name in EXPECTED:
        assert not any(word in name for word in NEVER), name

"""docs/api/openapi.json is the API the code serves: regenerate it with
`uv run cedesk openapi` after changing a route or schema."""

from cedesk.cli import ROOT, _openapi


def test_openapi_file_is_current() -> None:
    assert _openapi(ROOT / "docs" / "api" / "openapi.json", check=True) == 0

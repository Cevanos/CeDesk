"""docs/SETTINGS.md stays true to the code (md O6): every setting in `settings.py` has a
row; every name in it, in `deploy/env.example` and in `deploy/docker-compose.yml` is a
real setting (or a deploy-only variable); its "In compose" column says what the compose
file actually passes."""

import re
from pathlib import Path

from cedesk.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "SETTINGS.md").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / "deploy" / "env.example").read_text(encoding="utf-8")
COMPOSE = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")

DEPLOY_ONLY = {
    "POSTGRES_PASSWORD",
    "CEDESK_OWNER_PASSWORD",
    "CEDESK_APP_PASSWORD",
    "CEDESK_SITE",
    "CEDESK_MCP_SITE",
    "BACKUP_DIR",
    "CEDESK_FORWARDED_ALLOW_IPS",
    "CEDESK_ALLOW_INSECURE_HTTP",
}
SETTINGS = {f"CEDESK_{name.upper()}" for name in Settings.model_fields}


def rows() -> dict[str, list[str]]:
    """The doc's setting rows: name -> its cells."""
    found: dict[str, list[str]] = {}
    for line in DOC.splitlines():
        match = re.match(r"^\| `([A-Z0-9_]+)` \|", line)
        if match:
            assert match.group(1) not in found, f"{match.group(1)} has two rows"
            found[match.group(1)] = [c.strip() for c in line.strip("|").split("|")]
    return found


def passed_by_compose() -> set[str]:
    return set(re.findall(r"^\s+(CEDESK_[A-Z0-9_]+):", COMPOSE, re.M))


def test_every_setting_has_a_row() -> None:
    missing = SETTINGS - set(rows())
    assert not missing, f"Add to docs/SETTINGS.md: {sorted(missing)}"


def test_every_name_is_a_real_setting() -> None:
    known = SETTINGS | DEPLOY_ONLY
    in_doc = set(rows())
    in_env = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE, re.M))
    in_compose = passed_by_compose() | set(re.findall(r"\$\{([A-Z0-9_]+)", COMPOSE))
    for where, names in (("docs/SETTINGS.md", in_doc), ("deploy/env.example", in_env),
                         ("deploy/docker-compose.yml", in_compose)):  # fmt: skip
        unknown = names - known
        assert not unknown, f"{where} names settings that don't exist: {sorted(unknown)}"


def test_the_in_compose_column_matches_the_compose_file() -> None:
    passed = passed_by_compose()
    for name, cells in rows().items():
        if name not in SETTINGS:
            continue  # the deploy-only table has no such column
        says_no = cells[4] == "no"
        assert says_no == (name not in passed), f"{name}: 'In compose' says {cells[4]!r}"


def test_scheduled_reports_get_a_lower_query_limit_and_a_budget_per_pass() -> None:
    """The worker's one loop also sends ticket email, SLA alerts and pushes: a scheduled
    report may hold it for less than a member waiting on the page would."""
    fields = Settings.model_fields
    assert (
        fields["report_schedule_timeout_seconds"].default < fields["report_timeout_seconds"].default
    )
    assert fields["report_schedule_budget_seconds"].default == 20

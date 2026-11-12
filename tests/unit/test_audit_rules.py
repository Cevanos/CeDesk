"""Audit log rules (md S9): change lists, hidden secrets and the hash chain."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from cedesk.helpdesk.domain import audit as rules
from cedesk.helpdesk.domain.audit import GENESIS, HIDDEN, Entry


def entry(seq: int, **kw: object) -> Entry:
    base = Entry(
        seq=seq,
        at=datetime(2026, 11, 12, 9, 0, seq, tzinfo=UTC),
        actor_id=uuid4(),
        actor_name="Admin",
        ip="10.0.0.1",
        area="macros",
        action="created",
        target=f"Macro {seq}",
        target_id=None,
        detail={"steps": [1, 2]},
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def chain(n: int) -> list[tuple[Entry, bytes, bytes]]:
    rows, prev = [], GENESIS
    for seq in range(1, n + 1):
        e = entry(seq)
        h = rules.entry_hash(prev, e)
        rows.append((e, prev, h))
        prev = h
    return rows


def test_changes_lists_only_what_differs() -> None:
    before = {"name": "A", "enabled": True, "steps": [1]}
    after = {"name": "B", "enabled": True, "steps": [1]}
    assert rules.changes(before, after) == {"name": ["A", "B"]}
    assert rules.changes(before, before) == {}
    assert rules.changes(None, {"name": "A"}, fields=("name",)) == {"name": [None, "A"]}


def test_secrets_are_hidden_but_settings_about_them_are_not() -> None:
    before = {"idp_cert": "OLD", "api_key": "k1", "webhook_secret": "s1", "password_min": 10,
              "api_key_hint": "…1234", "key_hash": b"x", "password_expiry_days": None}  # fmt: skip
    after = {"idp_cert": "NEW", "api_key": "k2", "webhook_secret": "s2", "password_min": 12,
             "api_key_hint": "…9876", "key_hash": b"y", "password_expiry_days": 90}  # fmt: skip
    got = rules.changes(before, after)
    for secret in ("idp_cert", "api_key", "webhook_secret", "key_hash"):
        assert got[secret] == [HIDDEN, HIDDEN]
    assert got["password_min"] == [10, 12]
    assert got["api_key_hint"] == ["…1234", "…9876"]
    assert got["password_expiry_days"] == [None, 90]
    assert rules.snapshot({"name": "x", "token": "t"}, ("name", "token")) == {
        "name": "x",
        "token": HIDDEN,
    }


def test_long_values_are_cut() -> None:
    got = rules.changes({"body": ""}, {"body": "x" * 1000})["body"][1]
    assert len(got) == rules.MAX_VALUE and got.endswith("…")
    listed = rules.changes({"k": []}, {"k": ["word"] * 200})["k"][1]
    assert isinstance(listed, str) and len(listed) == rules.MAX_VALUE


def test_sets_compare_in_any_order() -> None:
    assert rules.json_safe({"c": frozenset({"b", "a", "c"})}) == {"c": ["a", "b", "c"]}
    assert rules.changes({"c": {"a", "b"}}, {"c": {"b", "a"}}) == {}


def test_an_intact_chain_verifies() -> None:
    assert rules.verify(chain(5)) == rules.Verified(True, 5, None)
    assert rules.verify([]) == rules.Verified(True, 0, None)


def test_an_edited_entry_breaks_the_chain() -> None:
    rows = chain(5)
    e, prev, h = rows[2]
    rows[2] = (replace(e, target="Macro hidden"), prev, h)
    assert rules.verify(rows) == rules.Verified(False, 2, 3)
    e, prev, h = chain(5)[3]
    rows = chain(5)
    rows[3] = (replace(e, detail={"steps": [1, 3]}), prev, h)
    assert rules.verify(rows) == rules.Verified(False, 3, 4)


def test_a_removed_or_reordered_entry_breaks_the_chain() -> None:
    rows = chain(5)
    assert rules.verify(rows[:2] + rows[3:]) == rules.Verified(False, 2, 4)
    assert rules.verify(rows[1:]) == rules.Verified(False, 0, 2)
    # A removed entry with the later ones renumbered and re-hashed by someone without
    # the earlier hashes: the link to the previous hash still fails.
    e, _, h = rows[3]
    forged = (replace(e, seq=3), rows[1][2], h)
    assert rules.verify([*rows[:2], forged]) == rules.Verified(False, 2, 3)


def test_the_time_reads_back_the_same() -> None:
    e = entry(1)
    local = replace(e, at=e.at.astimezone())
    assert rules.entry_hash(GENESIS, e) == rules.entry_hash(GENESIS, local)


def test_csv_cells_never_start_a_formula() -> None:
    from cedesk.helpdesk.service.audit import csv_cell

    for risky in ("=SUM(A1)", "+1", "-1", "@cmd", "\tx", "\rx"):
        assert csv_cell(risky) == "'" + risky
    assert csv_cell("Macro =SUM(A1)") == "Macro =SUM(A1)"
    assert csv_cell(42) == "42"

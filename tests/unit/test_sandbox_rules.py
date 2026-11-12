"""Sandbox compare (md S10): added, changed, removed and order, by id."""

from cedesk.helpdesk.domain.sandbox import SAMPLES, Section, check_unique, diff

A = {"id": "a", "name": "A", "steps": [1]}
B = {"id": "b", "name": "B", "steps": [2]}


def test_same_lists_are_empty() -> None:
    assert diff(Section.macros, [A, B], [A, B]).empty


def test_added_changed_removed() -> None:
    d = diff(Section.macros, [A, B], [{**A, "steps": [9]}, {"id": None, "name": "C", "steps": []}])
    assert d.added == ["C"] and d.removed == ["B"]
    assert [(c.id, c.changes) for c in d.changed] == [("a", {"steps": [[1], [9]]})]
    assert not d.order_changed  # the new one is last, where Deploy puts it


def test_order() -> None:
    assert diff(Section.macros, [A, B], [B, A]).order_changed
    new = {"id": None, "name": "C", "steps": []}
    assert diff(Section.macros, [A, B], [new, A, B]).order_changed
    assert not diff(Section.macros, [A, B], [A, B, new]).order_changed
    # Saved replies have no order.
    r1, r2 = {"id": "1", "shortcut": "a", "body": "x"}, {"id": "2", "shortcut": "b", "body": "y"}
    assert diff(Section.saved_replies, [r1, r2], [r2, r1]).empty


def test_an_id_live_no_longer_has_counts_as_added() -> None:
    gone = {"id": "z", "name": "Z", "steps": []}
    assert diff(Section.macros, [A], [A, gone]).added == ["Z"]


def test_unique() -> None:
    assert check_unique(Section.macros, [A, B]) is None
    assert check_unique(Section.macros, [A, {**B, "name": "a"}]) == "Two items are called a."
    assert check_unique(Section.macros, [A, {**B, "id": "a"}]) == "The same id is listed twice."


def test_samples_cover_every_kind_of_ticket() -> None:
    assert len({s.channel for s in SAMPLES}) == len(SAMPLES)

"""Custom fields, tags and ticket-number search, without a database."""

import pytest

from cedesk.helpdesk.domain.tickets import (
    FieldDef,
    FieldError,
    FieldKind,
    check_definitions,
    clean_tags,
    clean_values,
    label,
    number_in_query,
)

REFUND = [
    FieldDef("order_id", "Order ID", FieldKind.text, required=True),
    FieldDef("amount", "Amount", FieldKind.number),
    FieldDef("reason", "Reason", FieldKind.select, ("Damaged", "Late")),
]


def test_good_values_are_kept_and_blanks_dropped() -> None:
    values = {"order_id": " A-1 ", "amount": 12.5, "reason": ""}
    assert clean_values(REFUND, values) == {"order_id": "A-1", "amount": 12.5}


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({}, "Order ID is required"),
        ({"order_id": "A", "colour": "red"}, "no field colour"),
        ({"order_id": "A", "amount": "12"}, "Amount must be a number"),
        ({"order_id": "A", "amount": True}, "Amount must be a number"),
        ({"order_id": "A", "amount": float("nan")}, "Amount must be a number"),
        ({"order_id": "A", "reason": "Lost"}, "Reason must be one of"),
        ({"order_id": "x" * 2001}, "at most 2000"),
    ],
)
def test_bad_values_are_refused(values: dict[str, object], message: str) -> None:
    with pytest.raises(FieldError, match=message):
        clean_values(REFUND, values)


@pytest.mark.parametrize(
    "defs",
    [
        [FieldDef("a", "A", FieldKind.text), FieldDef("a", "B", FieldKind.text)],
        [FieldDef("Order ID", "A", FieldKind.text)],
        [FieldDef("a", " ", FieldKind.text)],
        [FieldDef("a", "A", FieldKind.select)],
        [FieldDef("a", "A", FieldKind.select, ("x", "x"))],
        [FieldDef("a", "A", FieldKind.text, ("x",))],
        [FieldDef(f"f{i}", "F", FieldKind.text) for i in range(31)],
    ],
)
def test_bad_field_lists_are_refused(defs: list[FieldDef]) -> None:
    with pytest.raises(FieldError):
        check_definitions(defs)


def test_tags_are_normalised() -> None:
    assert clean_tags([" Refund ", "refund", "Late Delivery"]) == ["refund", "late-delivery"]
    with pytest.raises(FieldError):
        clean_tags(["no!"])
    with pytest.raises(FieldError):
        clean_tags([f"t{i}" for i in range(21)])


def test_ticket_numbers() -> None:
    assert label(1001) == "T-1001"
    for q in ("T-1005", "t1005", " 1005 "):
        assert number_in_query(q) == 1005
    assert number_in_query("refund") is None

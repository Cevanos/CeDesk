"""Contact rules that need no database (feature-gap plan phase 2): field values by kind,
the list's filters and cursors, the merge plan, and CSV import and export."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cedesk.helpdesk.domain import contact_csv as csv_rules
from cedesk.helpdesk.domain import contact_fields as fields
from cedesk.helpdesk.domain import contact_merge as merge
from cedesk.helpdesk.domain import contacts as rules
from cedesk.helpdesk.domain.contact_fields import ContactField, ContactFieldError
from cedesk.helpdesk.domain.contact_fields import ContactFieldKind as K
from cedesk.helpdesk.domain.contacts import ContactError, ContactSort
from cedesk.helpdesk.domain.paging import CursorError

PLAN = ContactField("plan", "Plan", K.select, ("free", "pro"))
SEATS = ContactField("seats", "Seats", K.number)
RENEWS = ContactField("renews", "Renews", K.date)
TRIAL = ContactField("trial", "Trial", K.checkbox)
BIO = ContactField("bio", "Bio", K.text)
ALL = [PLAN, SEATS, RENEWS, TRIAL, BIO]


@pytest.mark.parametrize(
    ("f", "value", "stored"),
    [
        (PLAN, "pro", "pro"),
        (SEATS, 3, 3),
        (SEATS, 2.5, 2.5),
        (RENEWS, " 2027-01-31", "2027-01-31"),
        (TRIAL, False, False),
        (BIO, "  hi ", "hi"),
    ],
)
def test_values_are_kept_by_kind(f: ContactField, value: object, stored: object) -> None:
    assert fields.clean_value(f, value) == stored


@pytest.mark.parametrize(
    ("f", "value"),
    [
        (PLAN, "gold"),
        (SEATS, "3"),
        (SEATS, True),
        (SEATS, float("nan")),
        (RENEWS, "2027-02-30"),
        (RENEWS, "31/01/2027"),
        (TRIAL, "yes"),
        (BIO, "x" * 2001),
        (BIO, 5),
    ],
)
def test_wrong_values_are_refused(f: ContactField, value: object) -> None:
    with pytest.raises(ContactFieldError):
        fields.clean_value(f, value)


def test_text_from_a_csv_cell_or_query() -> None:
    assert fields.from_text(SEATS, "12") == 12 and fields.from_text(SEATS, "1.5") == 1.5
    assert fields.from_text(TRIAL, "Yes") is True and fields.from_text(TRIAL, "0") is False
    assert fields.from_text(PLAN, " ") is None
    with pytest.raises(ContactFieldError):
        fields.from_text(SEATS, "many")


def test_definitions_are_checked() -> None:
    with pytest.raises(ContactFieldError):
        fields.check_key("Plan")
    with pytest.raises(ContactFieldError):
        fields.clean_options(K.select, [])
    with pytest.raises(ContactFieldError):
        fields.clean_options(K.select, ["a", "a"])
    with pytest.raises(ContactFieldError):
        fields.clean_options(K.text, ["a"])
    assert fields.clean_label("  Plan   tier ") == "Plan tier"


def test_changes_clear_with_none_and_refuse_unknown_keys() -> None:
    assert fields.clean_changes(ALL, {"plan": None, "bio": "", "seats": 1}) == {
        "plan": None,
        "bio": None,
        "seats": 1,
    }
    with pytest.raises(ContactFieldError):
        fields.clean_changes(ALL, {"colour": "red"})


def test_filters_are_checked_and_stored_as_set() -> None:
    f = rules.list_filters(ALL, q="  ana ", vip=True, attributes={"plan": "pro", "trial": "true"})
    assert f.attributes == (("plan", "pro"), ("trial", True))
    assert f.stored() == {"q": "ana", "vip": True, "attributes": {"plan": "pro", "trial": True}}
    again = rules.filters_from_json(ALL, f.stored())
    assert again == f
    for bad in (
        {"colour": 1},
        {"vip": "yes"},
        {"sort": "x"},
        {"attributes": {"x": 1}},
        {"attributes": {"plan": "gold"}},
        {"created_from": "soon"},
        {"created_from": "2025-02-01", "created_to": "2025-01-01"},
    ):
        with pytest.raises(ContactError):
            rules.filters_from_json(ALL, bad)
    assert rules.parse_attribute_params(["plan:pro", "note:a:b"]) == {"plan": "pro", "note": "a:b"}
    with pytest.raises(ContactError):
        rules.parse_attribute_params(["plan"])
    assert rules.fields_used({"attributes": {"plan": "pro"}}) == {"plan"}


def test_cursors_belong_to_their_sort() -> None:
    cid = uuid4()
    for sort, key in (
        (ContactSort.name, "ana"),
        (ContactSort.tickets, 3),
        (ContactSort.recent, datetime.now(UTC).isoformat()),
    ):
        cursor = rules.encode_cursor(sort, key, cid)
        assert rules.decode_cursor(cursor, sort) == (key, cid)
        other = ContactSort.name if sort is not ContactSort.name else ContactSort.tickets
        with pytest.raises(CursorError):
            rules.decode_cursor(cursor, other)
    with pytest.raises(CursorError):
        rules.decode_cursor(rules.encode_cursor(ContactSort.tickets, "3", cid), ContactSort.tickets)
    at = datetime.now(UTC)
    assert rules.decode_timeline(rules.encode_timeline(at, "note", cid)) == (at, "note", cid)
    with pytest.raises(CursorError):
        rules.decode_timeline(rules.encode_cursor(ContactSort.name, "a", cid))


def _contact(**values: object) -> dict[str, object]:
    base = {
        "name": "Ana",
        "phone": None,
        "organization": None,
        "vip": False,
        "customer_since": datetime(2025, 1, 1, tzinfo=UTC),
        "attributes": {},
    }
    return base | values


def test_the_merge_plan() -> None:
    survivor = _contact(phone="1", attributes={"plan": "pro", "bio": ""})
    duplicate = _contact(
        name="Ana B",
        phone="2",
        organization="Shop",
        vip=True,
        customer_since=datetime(2024, 1, 1, tzinfo=UTC),
        attributes={"plan": "free", "bio": "Hi", "seats": 3},
    )
    p = merge.plan(survivor, duplicate)
    assert (p.phone, p.organization, p.vip) == ("1", "Shop", True)
    assert p.customer_since.year == 2024
    assert p.attributes == {"plan": "pro", "bio": "Hi", "seats": 3}
    assert p.taken == ["organization", "attributes.bio", "attributes.seats", "vip"]
    assert [(c.field, c.kept, c.dropped) for c in p.conflicts] == [
        ("name", "Ana", "Ana B"),
        ("phone", "1", "2"),
        ("attributes.plan", "pro", "free"),
    ]


def test_csv_rows_are_read_by_the_mapping() -> None:
    data = b"\xef\xbb\xbfEmail,Name,VIP,Seats\nA@X.test,Ana,yes,3\nbad,B,,\na@x.test,Again,,\n"
    headers, table = csv_rules.read_table(data)
    columns = csv_rules.guess_mapping(headers, [SEATS])
    assert columns.as_json() == {
        "email": "Email",
        "name": "Name",
        "vip": "VIP",
        "attributes": {"seats": "Seats"},
    }
    parsed = csv_rules.read_rows(headers, table, columns, [SEATS])
    assert [(r.line, r.email, r.vip, r.attributes) for r in parsed.rows] == [
        (2, "a@x.test", True, {"seats": 3})
    ]
    assert [(e.line, e.reason) for e in parsed.errors] == [
        (3, "No valid email address."),
        (4, "The same email as line 2."),
    ]
    with pytest.raises(csv_rules.BadFile):
        csv_rules.read_table(b"a,a\n1,2\n")
    with pytest.raises(csv_rules.BadMapping):
        csv_rules.check_mapping({"name": "Name"}, headers, [])


def test_the_export_is_safe_for_spreadsheets() -> None:
    row = {
        "id": uuid4(),
        "email": "a@x.test",
        "name": "=SUM(1)",
        "phone": "+1 555",
        "vip": True,
        "organization": None,
        "customer_since": None,
        "created_at": None,
        "ticket_count": 2,
        "open_ticket_count": 1,
        "other_emails": ["b@x.test"],
        "attributes": {"seats": 3, "trial": True},
    }
    cells = csv_rules.export_row(row, [SEATS, TRIAL])
    assert cells[2:6] == ["'=SUM(1)", "'+1 555", "", "true"]
    assert cells[-3:] == ["b@x.test", "3", "true"]
    assert csv_rules.export_header([SEATS])[-1] == "attr.seats"
    assert csv_rules.export_name(datetime(2026, 11, 20, 9, 5, tzinfo=UTC)) == (
        "contacts-20261120-0905.csv"
    )

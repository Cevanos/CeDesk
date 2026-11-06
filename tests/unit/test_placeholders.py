"""Placeholders in replies (T14)."""

from cedesk.helpdesk.domain.placeholders import EMPTY, render


def fill(body: str, fields: dict[str, object] | None = None, customer: str = "Ana Silva") -> str:
    return render(body, customer_name=customer, agent_name="Neha", fields=fields or {})


def test_known_placeholders_are_filled() -> None:
    body = (
        "Hi {{customer.first_name}} ({{ customer.name }}), "
        "order {{fields.order_id}}. {{agent.name}}"
    )
    assert fill(body, {"order_id": "A-77"}) == "Hi Ana (Ana Silva), order A-77. Neha"


def test_empty_values_show_a_dash() -> None:
    assert fill("{{fields.order_id}}") == EMPTY
    assert fill("{{fields.order_id}}", {"order_id": ""}) == EMPTY
    assert fill("{{customer.first_name}}", customer=" ") == EMPTY


def test_numbers_read_naturally() -> None:
    assert fill("{{fields.amount}}", {"amount": 20.0}) == "20"
    assert fill("{{fields.amount}}", {"amount": 19.5}) == "19.5"


def test_unknown_placeholders_stay_visible() -> None:
    assert fill("{{customer.phone}} {{oops}}") == "{{customer.phone}} {{oops}}"

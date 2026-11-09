"""File names, file types and storage keys, without a database."""

from uuid import UUID

import pytest

from cedesk.helpdesk.domain.attachments import clean_mime, clean_name, object_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("photo.jpg", "photo.jpg"),
        ("  Report Q3.pdf  ", "Report Q3.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\priya\\invoice.pdf", "invoice.pdf"),
        ("in\x00voice\n.pdf", "invoice.pdf"),
        ("invoice\u202efdp.exe", "invoicefdp.exe"),  # right-to-left override removed
        ("रसीद.pdf", "रसीद.pdf"),
        ("", "file"),
        ("..", "file"),
        ("folder/", "file"),
    ],
)
def test_names_keep_only_the_file_itself(raw: str, expected: str) -> None:
    assert clean_name(raw) == expected


def test_a_long_name_keeps_its_extension() -> None:
    name = clean_name("a" * 300 + ".pdf")
    assert len(name) == 255
    assert name.endswith(".pdf")
    assert len(clean_name("b" * 300)) == 255


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("image/png", "image/png"),
        ("Text/Plain; charset=UTF-8", "text/plain"),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", None),
        (None, "application/octet-stream"),
        ("", "application/octet-stream"),
        ("not a type", "application/octet-stream"),
        ("text/html\r\nX-Evil: 1", "application/octet-stream"),
    ],
)
def test_types_are_plain_type_slash_subtype(raw: str | None, expected: str | None) -> None:
    assert clean_mime(raw) == (expected or raw)


def test_keys_are_built_from_ids_only() -> None:
    ws = UUID("00000000-0000-0000-0000-000000000001")
    ticket = UUID("00000000-0000-0000-0000-000000000002")
    file = UUID("00000000-0000-0000-0000-000000000003")
    assert object_key(ws, ticket, file) == f"{ws}/{ticket}/{file}"

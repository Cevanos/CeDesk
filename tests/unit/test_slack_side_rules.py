"""Slack side conversation rules (md X5), no database: the signature's timestamp, which
thread messages come back (files included), and Slack's markup made readable."""

import hashlib
import hmac
from typing import Any

import pytest

from cedesk.helpdesk.domain import slack

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
NOW = 1_700_000_000.0


def _sig(stamp: str, body: bytes = b"{}") -> str:
    base = b"v0:" + stamp.encode() + b":" + body
    return "v0=" + hmac.new(SECRET.encode(), base, hashlib.sha256).hexdigest()


def test_a_good_signature() -> None:
    stamp = str(int(NOW))
    assert slack.signature_ok(SECRET, stamp, b"{}", _sig(stamp), NOW)


@pytest.mark.parametrize(
    "stamp",
    [
        "²",
        "1²",
        "\u0661\u0667\u0660\u0660\u0660\u0660\u0660\u0660\u0660\u0660",  # Arabic-Indic digits
        "",
        " 1700000000",
        "-1",
        "1700000000.5",
        str(int(NOW) + slack.SIGNATURE_WINDOW + 1),  # from the future, beyond the window
        str(int(NOW) - slack.SIGNATURE_WINDOW - 1),
    ],
)
def test_a_bad_timestamp_is_refused_not_an_error(stamp: str) -> None:
    assert slack.signature_ok(SECRET, stamp, b"{}", _sig(stamp), NOW) is False


def _event(**extra: Any) -> dict[str, Any]:
    event = {"type": "message", "channel": "C0WAREHOUSE", "thread_ts": "1700000000.000001",
             "ts": "1700000050.000200", "user": "U0RAVI", "text": "Here it is",
             **extra}  # fmt: skip
    return {"type": "event_callback", "event": event}


def test_a_file_share_reply_keeps_its_text_and_names_its_files() -> None:
    files = [{"name": "parcel.jpg", "url_private": "https://files.slack.com/x"},
             {"title": "label.pdf"}, {}, "junk"]  # fmt: skip
    reply = slack.thread_reply(_event(subtype="file_share", files=files))
    assert reply is not None
    assert slack.reply_body(reply) == (
        "Here it is\n[file: parcel.jpg]\n[file: label.pdf]\n[file: unnamed]"
    )
    only_a_photo = slack.thread_reply(_event(subtype="file_share", text="",
                                             files=[{"name": "a<b>.png"}]))  # fmt: skip
    assert only_a_photo is not None and slack.reply_body(only_a_photo) == "[file: a<b>.png]"
    no_text_key = _event(subtype="file_share", files=[{"name": "x.png"}])
    del no_text_key["event"]["text"]
    got = slack.thread_reply(no_text_key)
    assert got is not None and slack.reply_body(got) == "[file: x.png]"


@pytest.mark.parametrize(
    "extra",
    [
        {"bot_id": "B01"},
        {"subtype": "file_share", "bot_id": "B01"},
        {"subtype": "message_changed"},
        {"subtype": "message_deleted"},
        {"subtype": "channel_join"},
        {"subtype": "bot_message"},
    ],
)
def test_ignored_messages(extra: dict[str, Any]) -> None:
    assert slack.thread_reply(_event(**extra)) is None


def test_a_broadcast_reply_comes_back() -> None:
    reply = slack.thread_reply(_event(subtype="thread_broadcast"))
    assert reply is not None and slack.reply_body(reply) == "Here it is"


@pytest.mark.parametrize(
    ("raw", "plain"),
    [
        ("Ask <@U0RAVI> please", "Ask @Ravi please"),
        ("Ask <@U0NOBODY>", "Ask @U0NOBODY"),
        ("Ask <@U0NOBODY|old name>", "Ask @U0NOBODY"),
        (
            "See <https://x.example/a?b=1&amp;c=2|the label>",
            "See the label (https://x.example/a?b=1&c=2)",
        ),
        ("See <https://x.example>", "See https://x.example"),
        ("See <https://x.example|https://x.example>", "See https://x.example"),
        ("Mail <mailto:a@b.example|a@b.example>", "Mail a@b.example (mailto:a@b.example)"),
        ("In <#C0WAREHOUSE|warehouse>", "In #warehouse"),
        ("<!here> look", "@here look"),
        ("1 &lt; 2 &gt; 0 &amp; done", "1 < 2 > 0 & done"),
        ("&amp;lt; stays", "&lt; stays"),
        ("no markup", "no markup"),
    ],
)
def test_slack_markup_is_made_readable(raw: str, plain: str) -> None:
    assert slack.readable(raw, {"U0RAVI": "Ravi"}) == plain

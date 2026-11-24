"""CSAT rules (md P5, T15) that need no database: scores, comments, the survey email."""

from datetime import timedelta

from cedesk.helpdesk.domain.cevanos import TICKET_SOURCE as CEVANOS_SOURCE

LINK_LIFETIME = timedelta(days=30)
COMMENT_MAX = 2000
SCORES = range(1, 6)
HAPPY = 4  # 4 or 5 counts as happy in reports (md R1)


class BadRating(ValueError):
    pass


def check(score: int, comment: str | None) -> tuple[int, str | None]:
    if score not in SCORES:
        raise BadRating("Pick a score from 1 to 5.")
    text = (comment or "").strip()
    if len(text) > COMMENT_MAX:
        raise BadRating(f"A comment can be at most {COMMENT_MAX} characters.")
    return score, text or None


def rated_line(score: int, comment: str | None, *, changed: bool = False) -> str:
    """The system line agents see on the ticket."""
    line = f"Customer changed the rating to {score}/5" if changed else f"Customer rated {score}/5"
    if comment:
        short = " ".join(comment.split())
        line += f': "{short[:300]}{"…" if len(short) > 300 else ""}"'
    return line


def deliverable(email: str) -> bool:
    """Chat customers from Cevanos have a placeholder address that is never mailed; the
    widget asks them itself (md P7)."""
    return not email.lower().endswith(".invalid")


def survey_by_email(source: str | None, email: str) -> bool:
    """Whether the survey goes out by email. Never for a Cevanos chat, whose widget asks
    in the chat (md P7): once Cevanos says who the customer is (AI6) their address is real,
    so the ticket's source decides, not the address (staging, 23 Nov). Never to a
    placeholder address either."""
    return source != CEVANOS_SOURCE and deliverable(email)


def rate_link(base_url: str, token: str, score: int | None = None) -> str:
    """The token goes after `#`, so it never reaches a server log or a Referer. With a
    score, the page can save that score in one click."""
    query = f"?score={score}" if score is not None else ""
    return f"{base_url.rstrip('/')}/rate{query}#token={token}"


def survey_subject(label: str) -> str:
    return f"How did we do? [{label}]"


def survey_email(
    contact_name: str, workspace_name: str, label: str, subject: str, base_url: str, token: str
) -> str:
    first = contact_name.split()[0] if contact_name.split() else contact_name
    links = "\n".join(
        f"  {n} {'★' * n}{'☆' * (5 - n)}  {rate_link(base_url, token, n)}" for n in reversed(SCORES)
    )
    return (
        f"Hi {first},\n\n"
        f'Your request {label} "{subject}" was resolved. '
        f"How happy are you with {workspace_name} support? Pick one:\n\n"
        f"{links}\n\n"
        f"You can add a comment on the page that opens. The link works for 30 days.\n"
    )

"""Shared reading of the user-guide Markdown files (standard library only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs" / "user-guide" / "en"


@dataclass
class Page:
    path: Path
    meta: dict[str, object]
    body: str
    rel: str = field(init=False)

    def __post_init__(self) -> None:
        self.rel = self.path.relative_to(GUIDE).with_suffix("").as_posix()


def _scalar(v: str) -> object:
    v = v.strip()
    if v == "[]":
        return []
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def parse(text: str) -> tuple[dict[str, object], str]:
    """Front matter is `key: value` lines and `key:` followed by `  - item` lines."""
    if not text.startswith("---\n"):
        return {}, text
    head, _, body = text[4:].partition("\n---\n")
    meta: dict[str, object] = {}
    key = ""
    for raw in head.splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("- ") and key:
            cast = meta.setdefault(key, [])
            assert isinstance(cast, list)
            cast.append(_scalar(line.lstrip()[2:]))
        else:
            key, _, val = line.partition(":")
            key = key.strip()
            meta[key] = _scalar(val) if val.strip() else []
    return meta, body


def load_pages() -> list[Page]:
    pages = []
    for p in sorted(GUIDE.rglob("*.md")):
        meta, body = parse(p.read_text(encoding="utf-8"))
        pages.append(Page(p, meta, body))
    return pages

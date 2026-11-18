"""An imported article's HTML as Markdown, for the help center (md P6). No database.

An article's body is Markdown, and the help center shows it without raw HTML, so HTML from
another help desk would show its tags as text. `from_html` keeps the article's shape:
headings, paragraphs, line breaks, lists (nested too), bold, italics, links, images,
quotes, code and rules. A table becomes one line per row, its cells split by " | ".
Scripts, styles and forms go; any other tag goes and its text stays. The text is escaped,
so it reads as written: `order_id` stays `order_id`, not italics. Only http(s) and mailto
links and http(s) images are kept (a relative link keeps its text only). An embedded video
(`<iframe>`) becomes a link to it.

`from_text` is the same for a source that gives plain text only. `has_html` finds a body
still holding HTML tags (the repair of articles imported before this, md P6).
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

_BLOCKS = frozenset({
    "address", "article", "aside", "body", "center", "dd", "details", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "header", "html", "legend",
    "main", "nav", "p", "section", "summary",
})  # fmt: skip
_SKIP = frozenset({"head", "iframe", "noscript", "object", "script", "select", "style",
                   "svg", "template", "textarea", "title"})  # fmt: skip
_BOLD = frozenset({"b", "strong"})
_ITALIC = frozenset({"cite", "em", "i"})
_LINK = frozenset({"a"})
_HEADINGS = {f"h{n}": n for n in range(1, 7)}
_LISTS = frozenset({"ol", "ul"})
_IN_CELL = _BLOCKS | _LISTS | frozenset(_HEADINGS) | {"blockquote", "hr", "li", "pre"}

_SPACE = re.compile(r"[ \t\n\r\f\v\xa0]+")
_INLINE = re.compile(r"([\\`*_\[\]<])")
_ENTITY = re.compile(r"&(?=#?\w+;)")
_LEADING = re.compile(r"^([#>+=-])")
_NUMBERED = re.compile(r"^(\d+)([.)])(?=\s|$)")
_URL = re.compile(r"^(https?://|mailto:)\S+$", re.IGNORECASE)
_HTML = re.compile(
    r"(?<!\\)</?(a|b|blockquote|br|code|div|em|h[1-6]|hr|i|img|li|ol|p|pre|span|strong|"
    r"table|td|th|tr|u|ul)\b[^<>]*>",
    re.IGNORECASE,
)


def has_html(body: str) -> bool:
    """Whether a body still holds HTML tags (an escaped `\\<p>` is text, not a tag)."""
    return _HTML.search(body) is not None


def _escape(text: str) -> str:
    """Text that reads as written in Markdown: the characters that would mark it up are
    escaped (an entity-like `&copy;` too, so it isn't turned into ©)."""
    return _ENTITY.sub(r"\\&", _INLINE.sub(r"\\\1", text))


def _url(value: str | None, *, mail: bool = True) -> str | None:
    """A link target Markdown can hold, or None (relative, javascript:, data:...)."""
    value = (value or "").strip()
    if not _URL.match(value) or (not mail and value.lower().startswith("mailto:")):
        return None
    return value.replace("(", "%28").replace(")", "%29").replace("<", "%3C").replace(">", "%3E")


def _lines(text: str) -> str:
    """A block's lines: trimmed, a `<br>` kept as a hard break (two trailing spaces), an
    empty line as a paragraph break, and a line that would read as Markdown (`# 1`,
    `- x`, `1. x`) escaped."""
    out: list[str] = []
    for raw in text.split("\n"):
        line = re.sub(r" {2,}", " ", raw).strip()
        if line:
            line = _NUMBERED.sub(r"\1\\\2", _LEADING.sub(r"\\\1", line))
            if out and out[-1]:
                out[-1] += "  "
            out.append(line)
        elif out and out[-1]:
            out.append("")
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


@dataclass
class _Box:
    """What the next lines sit in: a quote (`> `) or a list item (its marker first,
    then an indent as wide as the marker)."""

    quote: bool
    first: str
    used: bool = False

    @property
    def rest(self) -> str:
        return self.first if self.quote else " " * len(self.first)


@dataclass
class _List:
    number: int  # the next item's number; -1 for bullets
    items: int = 0
    open_item: bool = False


@dataclass
class _Mark:
    """An open inline element and the Markdown written inside it so far."""

    tag: str
    href: str | None = None
    plain: bool = False  # bold inside bold: no second set of stars
    parts: list[str] = field(default_factory=list)


class _Converter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.text: list[str] = []  # the open block's inline Markdown
        self.marks: list[_Mark] = []
        self.boxes: list[_Box] = []
        self.lists: list[_List] = []
        self.gap = False  # a blank line is due before the next block
        self.skip = 0
        self.pre: list[str] | None = None
        self.code: list[str] | None = None
        self.heading = 0
        self.table = 0
        self.cells = 0

    # Writing

    def _out(self) -> list[str]:
        if self.code is not None:
            return self.code
        return self.marks[-1].parts if self.marks else self.text

    def _lead(self) -> str:
        lead = "".join(b.rest if b.used else b.first for b in self.boxes)
        for box in self.boxes:
            box.used = True
        return lead

    def _emit(self, block: str) -> None:
        if self.lines and self.gap:
            self.lines.append("".join(b.rest for b in self.boxes).rstrip())
        for line in block.split("\n"):
            self.lines.append(self._lead() + line if line else self._lead().rstrip())
        self.gap = True

    def _flush(self) -> None:
        """Ends the open block; inline tags still open end with it."""
        self._close_code()
        while self.marks:
            self._close_mark()
        block = _lines("".join(self.text))
        self.text = []
        if block and self.heading:
            block = "#" * self.heading + " " + block.replace("  \n", " ").replace("\n", " ")
        if block:
            self._emit(block)

    # Inline

    def _open_mark(self, tag: str, href: str | None = None) -> None:
        same = _BOLD if tag in _BOLD else _ITALIC if tag in _ITALIC else _LINK
        self.marks.append(_Mark(tag, href, plain=any(m.tag in same for m in self.marks)))

    def _close_mark(self) -> None:
        mark = self.marks.pop()
        inner = "".join(mark.parts)
        core = inner.strip()
        if not core:
            self._out().append(inner)
            return
        lead = inner[: len(inner) - len(inner.lstrip())]
        trail = inner[len(inner.rstrip()) :]
        if mark.tag in _LINK:
            written = f"[{core}]({mark.href})" if mark.href and not mark.plain else core
        elif mark.plain:
            written = core
        else:
            stars = "**" if mark.tag in _BOLD else "*"
            written = f"{stars}{core}{stars}"
        self._out().append(lead + written + trail)

    def _end_mark(self, group: frozenset[str]) -> None:
        """Closes the innermost open `group` tag, and any opened inside it."""
        for i in range(len(self.marks) - 1, -1, -1):
            if self.marks[i].tag in group:
                while len(self.marks) > i:
                    self._close_mark()
                return

    def _close_code(self) -> None:
        if self.code is None:
            return
        inner = re.sub(r" {2,}", " ", "".join(self.code)).strip()
        self.code = None
        if inner:
            fence = "`` " if "`" in inner else "`"
            self._out().append(f"{fence}{inner}{fence[::-1]}")

    # Lists and quotes

    def _pop_item(self) -> None:
        """Ends the innermost list's open item (an `<li>` the HTML left open, too)."""
        if self.lists and self.lists[-1].open_item:
            while self.boxes:
                if not self.boxes.pop().quote:
                    break
            self.lists[-1].open_item = False

    def _start_item(self) -> None:
        if not self.lists:
            self.lists.append(_List(-1))
        self._pop_item()
        current = self.lists[-1]
        if current.items:
            self.gap = False  # the items of one list sit together
        current.items += 1
        current.open_item = True
        if current.number < 0:
            marker = "- "
        else:
            marker = f"{current.number}. "
            current.number += 1
        self.boxes.append(_Box(quote=False, first=marker))

    # Parsing

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag in _SKIP:
            if tag == "iframe" and not self.skip and self.pre is None:
                self._video(found)
            self.skip += 1
            return
        if self.skip:
            return
        if self.pre is not None:
            if tag == "br":
                self.pre.append("\n")
            return
        if self.code is not None:
            if tag == "br":
                self.code.append(" ")
            return
        if tag == "br":
            self._out().append(" " if self.heading or self.table else "\n")
        elif tag == "img":
            src = _url(found.get("src"), mail=False)
            alt = _escape(_SPACE.sub(" ", found.get("alt") or "").strip())
            self._out().append(f"![{alt}]({src})" if src else alt)
        elif tag in _BOLD or tag in _ITALIC:
            self._open_mark(tag)
        elif tag in _LINK:
            self._open_mark(tag, _url(found.get("href")))
        elif tag == "code":
            self.code = []
        elif tag == "table":
            if not self.table:
                self._flush()
                self.cells = 0
            self.table += 1
        elif tag == "tr":
            self._end_row()
        elif tag in ("td", "th"):
            if self.cells:
                self._out().append(" | ")
            self.cells += 1
        elif self.table:
            if tag in _IN_CELL:
                self._out().append(" ")
        elif tag == "pre":
            self._flush()
            self.pre = []
        elif tag in _HEADINGS:
            self._flush()
            self.heading = _HEADINGS[tag]
        elif tag == "blockquote":
            self._flush()
            self.boxes.append(_Box(quote=True, first="> "))
        elif tag in _LISTS:
            self._flush()
            if self.boxes and not self.boxes[-1].quote:
                self.gap = False  # a list inside an item: no blank line before it
            start = found.get("start") or ""
            first = int(start) if start.isdigit() else 1
            self.lists.append(_List(first if tag == "ol" else -1))
        elif tag == "li":
            self._flush()
            self._start_item()
        elif tag == "hr":
            self._flush()
            self._emit("---")
        elif tag in _BLOCKS:
            self._flush()

    def _video(self, found: dict[str, str | None]) -> None:
        src = _url(found.get("src"), mail=False)
        if src:
            self._flush()
            self._emit(f"[{_escape(found.get('title') or src)}]({src})")

    def _end_row(self) -> None:
        if self.cells:
            self._out().append("\n")
        self.cells = 0

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag in _SKIP:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if self.pre is not None:
            if tag == "pre":
                self._end_pre()
            return
        if tag == "code":
            self._close_code()
        elif self.code is not None:
            return
        elif tag in _BOLD:
            self._end_mark(_BOLD)
        elif tag in _ITALIC:
            self._end_mark(_ITALIC)
        elif tag in _LINK:
            self._end_mark(_LINK)
        elif tag == "tr":
            self._end_row()
        elif tag == "table":
            self.table = max(0, self.table - 1)
            if not self.table:
                self._flush()
        elif self.table:
            return
        elif tag in _HEADINGS:
            self._flush()
            self.heading = 0
        elif tag == "blockquote":
            self._flush()
            while self.boxes:
                if self.boxes.pop().quote:
                    break
            self.gap = True
        elif tag == "li":
            self._flush()
            self._pop_item()
        elif tag in _LISTS:
            self._flush()
            self._pop_item()
            if self.lists:
                self.lists.pop()
            self.gap = True
        elif tag in _BLOCKS:
            self._flush()

    def _end_pre(self) -> None:
        body = "".join(self.pre or []).strip("\n")
        self.pre = None
        if body.strip():
            fence = "~~~" if "```" in body else "```"
            self._flush()
            self._emit(f"{fence}\n{body}\n{fence}")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.pre is not None:
            self.pre.append(data)
        elif self.code is not None:
            self.code.append(_SPACE.sub(" ", data))
        else:
            self._out().append(_escape(_SPACE.sub(" ", data)))

    def finish(self) -> str:
        self.close()
        if self.pre is not None:
            self._end_pre()
        self._flush()
        return "\n".join(self.lines).strip("\n")


def from_html(html: str | None) -> str:
    """Markdown for an article's HTML; empty for none."""
    if not html or not html.strip():
        return ""
    parser = _Converter()
    parser.feed(html)
    return parser.finish()


def from_text(text: str | None) -> str:
    """Markdown for plain text: escaped, its line breaks kept."""
    if not text:
        return ""
    return _lines("\n".join(_escape(_SPACE.sub(" ", line)) for line in text.split("\n")))

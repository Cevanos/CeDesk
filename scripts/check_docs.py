"""Check the user-guide Markdown against the code. Run: uv run python scripts/check_docs.py

Fails (exit 1) when an article:
- lacks required front matter, or has a slug that does not match its file name;
- lists a route that is not in docs/api/openapi.json;
- links a related article that does not exist, or an image file that does not exist;
- is marked `published` while it still has a screenshot placeholder;
- shows a raw internal value (inline code like `hard_bounce`) to readers.
"""

from __future__ import annotations

import json
import re
import sys

from docs_lib import GUIDE, ROOT, load_pages

REQUIRED = ("title", "slug", "topic", "order", "summary", "status", "updated", "routes", "related")
RAW_VALUE = re.compile(r"`[a-z]+(?:_[a-z]+)+`")


def routes_in_openapi() -> set[tuple[str, str]]:
    spec = json.loads((ROOT / "docs" / "api" / "openapi.json").read_text(encoding="utf-8"))
    return {
        (m.upper(), p)
        for p, ops in spec["paths"].items()
        for m in ops
        if m in ("get", "post", "put", "patch", "delete")
    }


def main() -> int:
    errors: list[str] = []
    known = routes_in_openapi()
    pages = load_pages()
    rels = {p.key for p in pages}
    articles = [p for p in pages if not p.path.name.startswith("_")]
    for p in articles:
        where = p.path.relative_to(ROOT).as_posix()
        for key in REQUIRED:
            if key not in p.meta:
                errors.append(f"{where}: front matter is missing '{key}'")
        if p.meta.get("status") not in ("draft", "published"):
            errors.append(f"{where}: status must be draft or published")
        stem = p.path.stem
        slug = stem.split("-", 1)[1] if stem[:2].isdigit() else stem
        if p.meta.get("slug") != slug:
            errors.append(f"{where}: slug '{p.meta.get('slug')}' does not match the file name")
        for r in p.meta.get("routes") or []:
            method, _, path = str(r).partition(" ")
            if (method, path) not in known:
                errors.append(f"{where}: route not in openapi.json: {r}")
        for rel in p.meta.get("related") or []:
            if str(rel) not in rels:
                errors.append(f"{where}: related article not found: {rel}")
        for link in re.findall(r"\]\(([^)#:]+\.md)\)", p.body):
            target = f"{p.path.parent.name}/{link.removesuffix('.md')}"
            if target not in rels:
                errors.append(f"{where}: link to an article that does not exist: {link}")
        for img in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", p.body):
            if not (p.path.parent / img).resolve().exists():
                errors.append(f"{where}: image not found: {img}")
        if p.meta.get("status") == "published" and "placeholder" in p.body.lower():
            errors.append(f"{where}: published but still has a placeholder")
        prose = re.sub(r"```.*?```", "", p.body, flags=re.S)
        for raw in sorted(set(RAW_VALUE.findall(prose))):
            errors.append(f"{where}: raw internal value shown to readers: {raw}")
    for e in errors:
        print("ERROR", e)
    print(f"{len(articles)} article(s) checked, {len(errors)} problem(s) in {GUIDE.name}/")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

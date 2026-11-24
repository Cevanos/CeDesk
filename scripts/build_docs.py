"""Build the user guide into plain static HTML. Run: uv run python scripts/build_docs.py

Reads docs/user-guide/en/**/*.md, writes docs/_site/ (git-ignored). Standard library only;
the Markdown it understands is: headings, paragraphs, bullet and numbered lists, tables,
block quotes, fenced code, images, **bold**, *italic*, `code` and [links](url).
"""

from __future__ import annotations

import html
import re
import shutil
from collections import defaultdict

from docs_lib import ROOT, Page, load_pages

OUT = ROOT / "docs" / "_site"
BRAND = "#6C55F6"

CSS = f"""
:root{{--brand:{BRAND};--ink:#0B1B36;--mute:#4B5563;--line:#E5E7EB;--tint:#F5F3FF;--bg:#F6F8FB}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.65 "Instrument Sans",Inter,system-ui,sans-serif}}
a{{color:var(--brand)}}a:hover{{text-decoration:underline}}
a:focus-visible,input:focus-visible{{outline:3px solid var(--brand);outline-offset:2px;border-radius:4px}}
.skip{{position:absolute;left:-9999px}}.skip:focus{{left:0;background:var(--brand);color:#fff;padding:10px}}
header{{background:#fff;border-bottom:1px solid var(--line)}}
.bar{{max-width:1200px;margin:0 auto;padding:14px 24px;display:flex;flex-wrap:wrap;gap:20px;align-items:center}}
.logo{{font-weight:700;font-size:18px;color:var(--ink);text-decoration:none}}.logo b{{color:var(--brand)}}
.bar nav{{margin-left:auto;display:flex;gap:20px}}.bar nav a{{color:var(--mute);text-decoration:none}}
.wrap{{max-width:1200px;margin:0 auto;padding:32px 24px 64px;display:flex;flex-wrap:wrap;gap:40px;align-items:flex-start}}
aside{{flex:1 1 220px;max-width:260px;font-size:14px;display:flex;flex-direction:column;gap:2px}}
aside a{{padding:6px 10px;border-radius:6px;color:#374151;text-decoration:none}}
aside a.on{{background:var(--tint);color:#3B2A9E;font-weight:600}}
main{{flex:999 1 560px;min-width:0;max-width:760px;background:#fff;border:1px solid var(--line);
border-radius:12px;padding:32px}}
h1{{margin:0 0 8px;font-size:34px;line-height:1.2}}h2{{font-size:24px;margin:32px 0 8px}}
.meta{{color:var(--mute);font-size:14px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;font-size:15px;display:block;overflow-x:auto}}
th,td{{padding:10px 14px;text-align:left;border:1px solid var(--line)}}th{{background:#F9FAFB}}
blockquote{{margin:16px 0;padding:12px 16px;border-left:4px solid var(--brand);background:var(--tint);border-radius:8px}}
blockquote p{{margin:0}}code{{font:14px "JetBrains Mono",monospace;background:#F3F4F6;padding:1px 6px;border-radius:4px}}
pre{{background:#0B1B36;color:#E5E7EB;padding:16px;border-radius:10px;overflow-x:auto}}pre code{{background:none;padding:0}}
img{{max-width:100%}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px}}
.card{{display:block;padding:20px;border:1px solid var(--line);border-radius:12px;background:#fff;
color:var(--ink);text-decoration:none}}.card small{{color:var(--mute)}}
.draft{{background:#FEF3C7;color:#92400E;padding:2px 8px;border-radius:6px;font-size:12px}}
@media (max-width:900px){{aside{{max-width:none;flex-basis:100%;order:2}}main{{order:1;flex-basis:100%;padding:20px}}
h1{{font-size:28px}}}}
"""


def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img alt="\1" src="\2">', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    # A link to another article is written as slug.md (or ../topic/slug.md); the built
    # page is slug.html.
    return re.sub(r'href="([^":]+)\.md"', r'href="\1.html"', s)


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def render(md: str) -> tuple[str, list[tuple[str, str]]]:
    out: list[str] = []
    toc: list[tuple[str, str]] = []
    lines = md.strip("\n").splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            code = html.escape("\n".join(lines[i + 1 : j]))
            out.append(f"<pre><code>{code}</code></pre>")
            i = j + 1
        elif ln.startswith("#"):
            n = len(ln) - len(ln.lstrip("#"))
            text = ln[n:].strip()
            if n == 2:
                toc.append((slugify(text), text))
            out.append(f'<h{n} id="{slugify(text)}">{inline(text)}</h{n}>')
            i += 1
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"-+", c) for c in cells):
                    rows.append(cells)
                i += 1
            head, body = rows[0], rows[1:]
            th = "".join(f"<th>{inline(c)}</th>" for c in head)
            tr = "".join(
                "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body
            )
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>")
        elif re.match(r"(- |\d+\. )", ln):
            tag = "ul" if ln.startswith("- ") else "ol"
            items = []
            while i < len(lines) and re.match(r"(- |\d+\. )", lines[i]):
                items.append(re.sub(r"^(- |\d+\. )", "", lines[i]))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(t)}</li>" for t in items) + f"</{tag}>")
        elif ln.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i].lstrip("> "))
                i += 1
            out.append(f"<blockquote><p>{inline(' '.join(quote))}</p></blockquote>")
        elif ln.strip():
            para = []
            while (
                i < len(lines)
                and lines[i].strip()
                and not re.match(r"(#|\||>|```|- |\d+\. )", lines[i])
            ):
                para.append(lines[i])
                i += 1
            out.append(f"<p>{inline(' '.join(para))}</p>")
        else:
            i += 1
    return "\n".join(out), toc


def shell(title: str, side: str, body: str, root: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} · CeDesk Help Center</title>
<style>{CSS}</style></head><body><a class="skip" href="#content">Skip to content</a>
<header><div class="bar"><a class="logo" href="{root}index.html">Ce<b>Desk</b> Help Center</a>
<nav><a href="{root}index.html">User guide</a><a href="{root}glossary.html">Glossary</a></nav></div></header>
<div class="wrap">{side}<main id="content">{body}</main></div></body></html>"""


def main() -> None:
    pages = load_pages()
    topics = {p.path.parent.name: p for p in pages if p.path.name == "_topic.md"}
    arts = [p for p in pages if not p.path.name.startswith("_") and p.meta.get("topic")]
    by_topic: dict[str, list[Page]] = defaultdict(list)
    for a in sorted(arts, key=lambda a: int(str(a.meta["order"]))):
        by_topic[str(a.meta["topic"])].append(a)
    order = sorted(topics, key=lambda t: int(str(topics[t].meta["order"])))
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    def side(current: str, root: str) -> str:
        links = "".join(
            f'<a class="{"on" if t == current else ""}" href="{root}{t}/index.html">'
            f"{html.escape(str(topics[t].meta['title']))}</a>"
            for t in order
        )
        return f"<aside aria-label='Topics'>{links}</aside>"

    cards = "".join(
        f'<a class="card" href="{t}/index.html"><strong>{html.escape(str(topics[t].meta["title"]))}</strong>'
        f"<p>{html.escape(str(topics[t].meta['summary']))}</p><small>{len(by_topic[t])} articles</small></a>"
        for t in order
    )
    (OUT / "index.html").write_text(
        shell(
            "Home",
            "",
            f"<h1>How can we help you?</h1><p>Browse by topic.</p><div class='grid'>{cards}</div>",
            "",
        ),
        encoding="utf-8",
    )
    for t in order:
        items = "".join(
            f'<li><a href="{a.meta["slug"]}.html">{html.escape(str(a.meta["title"]))}</a>'
            f" — {html.escape(str(a.meta['summary']))}</li>"
            for a in by_topic[t]
        )
        body = f"<h1>{html.escape(str(topics[t].meta['title']))}</h1><p>{html.escape(str(topics[t].meta['summary']))}</p><ol>{items}</ol>"
        (OUT / t).mkdir()
        (OUT / t / "index.html").write_text(
            shell(str(topics[t].meta["title"]), side(t, "../"), body, "../"), encoding="utf-8"
        )
    for a in arts:
        t = str(a.meta["topic"])
        content, _toc = render(a.body)
        badge = ' <span class="draft">Draft</span>' if a.meta.get("status") == "draft" else ""
        meta = f"Last updated {a.meta['updated']} · Who: {html.escape(str(a.meta.get('who', '')))}"
        body = f"<h1>{html.escape(str(a.meta['title']))}{badge}</h1><div class='meta'>{meta}</div>{content}"
        (OUT / t / f"{a.meta['slug']}.html").write_text(
            shell(str(a.meta["title"]), side(t, "../"), body, "../"), encoding="utf-8"
        )
    gl = next(p for p in pages if p.path.name == "glossary.md")
    content, _ = render(gl.body)
    (OUT / "glossary.html").write_text(
        shell("Glossary", "", f"<h1>Glossary</h1><p>{gl.meta['summary']}</p>{content}", ""),
        encoding="utf-8",
    )
    print(f"built {len(arts)} article(s) in {len(order)} topic(s) -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

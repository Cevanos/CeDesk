"""The API reference, generated from docs/api/openapi.json (standard library only).

One page per group of routes (the OpenAPI "tag") and an index. Nothing here is written by
hand, so the reference cannot disagree with the API. Used by build_docs.py.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from docs_lib import ROOT

SPEC = ROOT / "docs" / "api" / "openapi.json"
METHODS = ("get", "post", "put", "patch", "delete")
MAX_DEPTH = 3

Shell = Callable[[str, str, str, str], str]

EXTRA_CSS = """
.op{border:1px solid var(--line);border-radius:12px;margin:24px 0;padding:20px;background:#fff}
.op h3{margin:0 0 6px;font-size:18px}.route{font:14px "JetBrains Mono",monospace;word-break:break-all}
.verb{display:inline-block;min-width:58px;text-align:center;padding:2px 8px;border-radius:6px;color:#fff;
font:700 12px "JetBrains Mono",monospace;margin-right:8px}
.get{background:#0E7490}.post{background:#15803D}.put{background:#B45309}.patch{background:#7C3AED}.delete{background:#B91C1C}
.op h4{margin:18px 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute)}
.op table{font-size:14px}.op td:first-child{font:13px "JetBrains Mono",monospace;white-space:nowrap}
.op pre{font-size:13px}.req{color:#B91C1C;font-size:12px}
"""


def esc(s: object) -> str:
    return html.escape(str(s), quote=False)


def md(s: str) -> str:
    """Descriptions are plain text with `code`; nothing else."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(s))


def slug(tag: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")


class Spec:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.schemas: dict[str, Any] = data["components"]["schemas"]

    def deref(self, schema: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        ref = schema.get("$ref")
        if isinstance(ref, str):
            name = ref.rsplit("/", 1)[-1]
            return self.schemas[name], name
        return schema, None

    def type_of(self, schema: dict[str, Any]) -> str:
        s, name = self.deref(schema)
        if name:
            return "enum" if "enum" in s else name
        if "anyOf" in s or "oneOf" in s:
            parts = [self.type_of(x) for x in s.get("anyOf", s.get("oneOf", []))]
            return " or ".join(dict.fromkeys(parts))
        if "enum" in s:
            return "enum"
        if s.get("type") == "array":
            return f"list of {self.type_of(s.get('items', {}))}"
        base = str(s.get("type", "object"))
        return f"{base} ({s['format']})" if "format" in s else base

    def rows(
        self, schema: dict[str, Any], prefix: str = "", depth: int = 0, seen: tuple[str, ...] = ()
    ) -> list[tuple[str, str, bool, str]]:
        s, name = self.deref(schema)
        if name:
            if name in seen:
                return []
            seen = (*seen, name)
        for key in ("anyOf", "oneOf"):
            for option in s.get(key, []):
                inner, _ = self.deref(option)
                if inner.get("type") == "object" or "properties" in inner:
                    return self.rows(option, prefix, depth, seen)
        if s.get("type") == "array":
            return (
                self.rows(s.get("items", {}), prefix + "[]", depth, seen)
                if depth < MAX_DEPTH
                else []
            )
        out: list[tuple[str, str, bool, str]] = []
        required = set(s.get("required", []))
        for key, prop in s.get("properties", {}).items():
            inner, _ = self.deref(prop)
            note = str(prop.get("description") or inner.get("description") or "")
            if "enum" in inner:
                note = (note + " One of: " + ", ".join(str(v) for v in inner["enum"])).strip()
            path = f"{prefix}{key}"
            out.append((path, self.type_of(prop), key in required, note))
            if depth < MAX_DEPTH and (
                inner.get("type") == "object"
                or "properties" in inner
                or (inner.get("type") == "array" and "items" in inner)
            ):
                out.extend(self.rows(prop, f"{path}.", depth + 1, seen))
        return out

    def example(self, schema: dict[str, Any], depth: int = 0, seen: tuple[str, ...] = ()) -> Any:
        s, name = self.deref(schema)
        if name:
            if name in seen:
                return None
            seen = (*seen, name)
        for key in ("anyOf", "oneOf"):
            options = [o for o in s.get(key, []) if o.get("type") != "null"]
            if options:
                return self.example(options[0], depth, seen)
        if "enum" in s:
            return s["enum"][0]
        if "default" in s and s["default"] is not None:
            return s["default"]
        kind = s.get("type")
        if kind == "string":
            return {
                "uuid": "00000000-0000-0000-0000-000000000000",
                "date-time": "2026-11-24T10:00:00Z",
            }.get(s.get("format", ""), "string")
        if kind in ("integer", "number"):
            return max(s.get("minimum", 0), 0)
        if kind == "boolean":
            return False
        if kind == "array":
            return [self.example(s.get("items", {}), depth + 1, seen)] if depth < MAX_DEPTH else []
        if kind == "object" or "properties" in s:
            if depth >= MAX_DEPTH:
                return {}
            required = list(s.get("required", []))
            return {
                k: self.example(s["properties"][k], depth + 1, seen)
                for k in required
                if k in s.get("properties", {})
            }
        return None


def table(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{h}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>"


def operation(spec: Spec, path: str, method: str, op: dict[str, Any]) -> str:
    out = [
        f'<div class="op" id="{method}-{slug(path)}"><h3>{esc(op.get("summary", ""))}</h3>',
        f'<p class="route"><span class="verb {method}">{method.upper()}</span>{esc(path)}</p>',
    ]
    if op.get("description"):
        out.append(f"<p>{md(op['description'])}</p>")
    params = op.get("parameters", [])
    if params:
        rows = [
            [
                esc(p["name"])
                + (' <span class="req">required</span>' if p.get("required") else ""),
                esc(p["in"]),
                esc(spec.type_of(p.get("schema", {}))),
                md(str(p.get("description") or "")),
            ]
            for p in params
        ]
        out.append("<h4>Parameters</h4>" + table(["Name", "In", "Type", "Description"], rows))
    body = op.get("requestBody", {}).get("content", {})
    media = body.get("application/json")
    if media is not None:
        fields = spec.rows(media.get("schema", {}))
        out.append("<h4>Request body (JSON)</h4>")
        if fields:
            out.append(
                table(
                    ["Field", "Type", "Description"],
                    [
                        [
                            esc(n) + (' <span class="req">required</span>' if r else ""),
                            esc(t),
                            md(d),
                        ]
                        for n, t, r, d in fields
                    ],
                )
            )
        example = json.dumps(spec.example(media.get("schema", {})), indent=2)
        cmd = (
            f'curl -X {method.upper()} "$CEDESK_URL{path}" \\\n'
            f'  -H "Authorization: Bearer $CEDESK_KEY" \\\n'
            f'  -H "Content-Type: application/json" \\\n'
            f"  -d '{example}'"
        )
        out.append(f"<pre><code>{esc(cmd)}</code></pre>")
    elif "application/octet-stream" in body or body:
        out.append("<h4>Request body</h4><p>The file itself, not a form. See the description.</p>")
    else:
        cmd = f'curl "$CEDESK_URL{path}" \\\n  -H "Authorization: Bearer $CEDESK_KEY"'
        if method != "get":
            cmd = cmd.replace("curl ", f"curl -X {method.upper()} ", 1)
        out.append(f"<h4>Example</h4><pre><code>{esc(cmd)}</code></pre>")
    resp_rows = []
    for code, r in op.get("responses", {}).items():
        content = r.get("content", {}).get("application/json")
        shape = esc(spec.type_of(content["schema"])) if content and "schema" in content else ""
        resp_rows.append([esc(code), md(str(r.get("description", ""))), shape])
    out.append("<h4>Responses</h4>" + table(["Code", "Meaning", "Body"], resp_rows))
    for code, r in op.get("responses", {}).items():
        content = r.get("content", {}).get("application/json")
        if code.startswith("2") and content and "schema" in content:
            fields = spec.rows(content["schema"])
            if fields:
                out.append(
                    f"<h4>Response fields ({esc(code)})</h4>"
                    + table(
                        ["Field", "Type", "Description"],
                        [[esc(n), esc(t), md(d)] for n, t, _r, d in fields],
                    )
                )
            break
    if op.get("security") == []:
        out.append("<p><em>No sign-in needed.</em></p>")
    out.append("</div>")
    return "".join(out)


INTRO = """
<h1>API reference</h1>
<p>Everything CeDesk does is available over HTTP. This reference is generated from the API
description, so it always matches the code. Every path starts with <code>/api/v1</code>.</p>
<h2 id="auth">Signing in</h2>
<p>Send a bearer token in the <code>Authorization</code> header. Use the token from
<code>POST /api/v1/auth/sign-in</code>, or an API key that starts with <code>cdk_</code>.
An Admin makes keys under Settings. A key acts as the Admin who made it, within its level.</p>
<pre><code>curl "$CEDESK_URL/api/v1/tickets" -H "Authorization: Bearer $CEDESK_KEY"</code></pre>
<h2 id="errors">Errors</h2>
<p>A refused request answers with a 4xx code and a JSON body that has an error code and a
message you can show to a person. <code>422</code> means the request is not valid,
<code>429</code> means slow down (read <code>Retry-After</code>), and <code>401</code> or
<code>403</code> mean you are not signed in or not allowed.</p>
<h2 id="retries">Retries and idempotency</h2>
<p>Routes that create things accept an <code>Idempotency-Key</code> header. Send the same key
when you retry and you get what the first request made, not a second copy.</p>
<h2 id="paging">Paging</h2>
<p>Lists return <code>next_cursor</code>. Send it back as <code>cursor</code>, with the same
filters, for the next page.</p>
<h2 id="groups">Groups of routes</h2>
"""


def build(out: Path, shell: Shell) -> int:
    """Write out/api/*.html. Returns the number of operations documented."""
    spec = Spec(json.loads(SPEC.read_text(encoding="utf-8")))
    groups: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for path, item in spec.data["paths"].items():
        for method in METHODS:
            op = item.get(method)
            if op is not None:
                groups.setdefault(op.get("tags", ["other"])[0], []).append((path, method, op))
    (out / "api").mkdir(parents=True, exist_ok=True)
    names = sorted(groups)
    for ops in groups.values():
        ops.sort(key=lambda o: (o[0].count("/"), o[0], METHODS.index(o[1])))
    side = (
        "<aside aria-label='API groups'>"
        + "".join(f'<a href="{slug(t)}.html">{esc(t.capitalize())}</a>' for t in names)
        + "</aside>"
    )
    cards = "".join(
        f'<li><a href="{slug(t)}.html">{esc(t.capitalize())}</a> ({len(groups[t])})</li>'
        for t in names
    )
    index = INTRO + f"<ul>{cards}</ul>"
    (out / "api" / "index.html").write_text(
        shell("API reference", "", index, "../"), encoding="utf-8"
    )
    total = 0
    for tag in names:
        ops = "".join(operation(spec, p, m, o) for p, m, o in groups[tag])
        total += len(groups[tag])
        body = f"<h1>{esc(tag.capitalize())}</h1><p>{len(groups[tag])} routes.</p>{ops}"
        (out / "api" / f"{slug(tag)}.html").write_text(
            shell(f"{tag.capitalize()} · API reference", side, body, "../"),
            encoding="utf-8",
        )
    return total

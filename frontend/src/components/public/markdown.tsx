import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Small, safe Markdown renderer for help-center articles.
 *
 * It builds React elements and never uses dangerouslySetInnerHTML, so raw HTML in the
 * source is shown as plain text, not run. Links only keep http(s) and mailto targets.
 * Supported: # headings, paragraphs, **bold**, *italic*, `code`, fenced code, > quotes,
 * - / 1. lists and [text](url) links.
 */

const SAFE_LINK = /^(https?:\/\/|mailto:)/i;

function inline(text: string, keyPrefix: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0;
  let n = 0;
  for (const m of text.matchAll(re)) {
    const i = m.index ?? 0;
    if (i > last) out.push(text.slice(last, i));
    const tok = m[0];
    const key = `${keyPrefix}-${n++}`;
    if (m[1]) {
      out.push(
        <code key={key} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[0.9em]">
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (m[2]) {
      out.push(<strong key={key}>{inline(tok.slice(2, -2), key)}</strong>);
    } else if (m[3]) {
      out.push(<em key={key}>{inline(tok.slice(1, -1), key)}</em>);
    } else {
      const close = tok.indexOf("](");
      const label = tok.slice(1, close);
      const href = tok.slice(close + 2, -1);
      out.push(
        SAFE_LINK.test(href) ? (
          <a
            key={key}
            href={href}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="font-medium text-primary-ink underline underline-offset-2 hover:no-underline"
          >
            {label}
          </a>
        ) : (
          // Anything else (javascript:, data:, relative) stays as plain text.
          <React.Fragment key={key}>{label}</React.Fragment>
        ),
      );
    }
    last = i + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block =
  | { t: "h"; level: number; text: string }
  | { t: "p"; text: string }
  | { t: "ul" | "ol"; items: string[] }
  | { t: "quote"; text: string }
  | { t: "code"; text: string };

function parse(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";
    if (!line.trim()) {
      i++;
      continue;
    }
    if (line.startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !(lines[i] ?? "").startsWith("```")) body.push(lines[i++] ?? "");
      i++;
      blocks.push({ t: "code", text: body.join("\n") });
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      blocks.push({ t: "h", level: h[1]!.length, text: h[2] ?? "" });
      i++;
      continue;
    }
    if (/^>\s?/.test(line)) {
      const q: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i] ?? "")) q.push((lines[i++] ?? "").replace(/^>\s?/, ""));
      blocks.push({ t: "quote", text: q.join(" ") });
      continue;
    }
    const list = /^(\s*)([-*]|\d+\.)\s+/.exec(line);
    if (list) {
      const ordered = /\d/.test(list[2] ?? "");
      const items: string[] = [];
      while (i < lines.length && /^(\s*)([-*]|\d+\.)\s+/.test(lines[i] ?? "")) {
        items.push((lines[i++] ?? "").replace(/^(\s*)([-*]|\d+\.)\s+/, ""));
      }
      blocks.push({ t: ordered ? "ol" : "ul", items });
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      (lines[i] ?? "").trim() &&
      !/^(#{1,4}\s|>|```|(\s*)([-*]|\d+\.)\s)/.test(lines[i] ?? "")
    ) {
      para.push((lines[i++] ?? "").trim());
    }
    blocks.push({ t: "p", text: para.join(" ") });
  }
  return blocks;
}

const HEADING = [
  "",
  "mt-8 text-2xl font-semibold tracking-tight",
  "mt-7 text-xl font-semibold tracking-tight",
  "mt-6 text-lg font-semibold",
  "mt-5 text-base font-semibold",
];

export function Markdown({ source, className }: { source: string; className?: string }) {
  return (
    <div className={cn("space-y-4 text-[15px] leading-7 text-foreground-2", className)}>
      {parse(source).map((b, n) => {
        const key = `b${n}`;
        switch (b.t) {
          case "h": {
            const Tag = `h${Math.min(b.level + 1, 5)}` as "h2";
            return (
              <Tag key={key} className={cn("text-foreground", HEADING[b.level])}>
                {inline(b.text, key)}
              </Tag>
            );
          }
          case "ul":
          case "ol": {
            const Tag = b.t;
            return (
              <Tag
                key={key}
                className={cn("space-y-1.5 ps-6", b.t === "ul" ? "list-disc" : "list-decimal")}
              >
                {b.items.map((it, j) => (
                  <li key={j}>{inline(it, `${key}-${j}`)}</li>
                ))}
              </Tag>
            );
          }
          case "quote":
            return (
              <blockquote
                key={key}
                className="rounded-e-lg border-s-4 border-primary/40 bg-primary-soft/50 px-4 py-2.5 text-foreground-2"
              >
                {inline(b.text, key)}
              </blockquote>
            );
          case "code":
            return (
              <pre
                key={key}
                className="overflow-x-auto rounded-xl bg-nav p-4 font-mono text-[13px] leading-6 text-white/90"
              >
                {b.text}
              </pre>
            );
          default:
            return <p key={key}>{inline(b.text, key)}</p>;
        }
      })}
    </div>
  );
}

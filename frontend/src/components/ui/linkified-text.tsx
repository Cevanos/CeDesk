import type { ReactNode } from "react";

const URL_RE = /https?:\/\/[^\s<>"']+/gi;
const TRAILING = /[.,;:!?)\]}]+$/;

/** Split text into plain strings and http(s) links. Never produces HTML. */
export function linkify(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const match of text.matchAll(URL_RE)) {
    const start = match.index ?? 0;
    let url = match[0];
    // Trailing punctuation isn't part of the link; keep a ")" if the URL has its "(".
    let m: RegExpMatchArray | null;
    while ((m = url.match(TRAILING))) {
      if (m[0].startsWith(")") && url.includes("(")) break;
      url = url.slice(0, -m[0].length);
    }
    if (!/^https?:\/\/[^/]/i.test(url)) continue;
    if (start > last) out.push(text.slice(last, start));
    out.push(
      <a
        key={i++}
        href={url}
        target="_blank"
        rel="noopener noreferrer nofollow"
        className="underline underline-offset-2 hover:opacity-80"
      >
        {url}
      </a>,
    );
    last = start + url.length; // trailing punctuation stays in the next text slice
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function LinkifiedText({ text }: { text: string }) {
  return <>{linkify(text)}</>;
}

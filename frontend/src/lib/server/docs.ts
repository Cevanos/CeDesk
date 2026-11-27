import "server-only";

import { promises as fs } from "node:fs";
import path from "node:path";

/**
 * Loads the user guide and API reference that live as static HTML in `public/docs` (built by
 * the CDESK repository) and hands them to the Next.js docs pages. The HTML is trusted build
 * output from our own repository; it is never user input.
 */
const ROOT = path.join(process.cwd(), "public", "docs");

export type DocPage = {
  slug: string[];
  title: string;
  description: string;
  html: string;
  toc: { id: string; text: string }[];
  draft: boolean;
  meta: string;
  section: string | null;
};

export type DocSection = { slug: string; title: string; description: string; count: string };
export type DocLink = { href: string; title: string; summary: string };

const decode = (s: string) =>
  s
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
const strip = (s: string) =>
  decode(s.replace(/<[^>]+>/g, ""))
    .replace(/\s+/g, " ")
    .trim();
// The generator wrote some separators as a broken character; show a middle dot / dash.
const fixChars = (s: string) => s.replace(/ \uFFFD /g, " — ");

export function slugToFile(slug: string[]): string | null {
  if (slug.some((s) => !/^[a-z0-9][a-z0-9._-]*$/i.test(s) || s.includes(".."))) return null;
  const rel = slug.length === 0 ? "index" : slug.join("/");
  return path.join(ROOT, `${rel}.html`);
}

async function read(file: string): Promise<string | null> {
  try {
    return fixChars(await fs.readFile(file, "utf8"));
  } catch {
    return null;
  }
}

/** `../api/index.html#x` -> `/docs/api#x`, resolved against the directory of the file it is in. */
export function rewriteHref(href: string, dirParts: string[]): string {
  if (/^([a-z]+:|#|\/\/)/i.test(href)) return href;
  const [rawPath = "", hash = ""] = href.split("#");
  const parts = [...dirParts];
  for (const seg of rawPath.split("/")) {
    if (seg === "..") parts.pop();
    else if (seg && seg !== ".") parts.push(seg);
  }
  if (parts.at(-1) === "index.html") parts.pop();
  else if (parts.length) parts[parts.length - 1] = (parts.at(-1) ?? "").replace(/\.html$/, "");
  return `/docs${parts.length ? `/${parts.join("/")}` : ""}${hash ? `#${hash}` : ""}`;
}

let dirs: Set<string> | null = null;
async function loadDirs() {
  if (dirs) return dirs;
  const entries = await fs.readdir(ROOT, { withFileTypes: true });
  dirs = new Set(entries.filter((e) => e.isDirectory()).map((e) => e.name));
  return dirs;
}

export async function getDoc(slug: string[]): Promise<DocPage | null> {
  const dirNames = await loadDirs();
  let file = slugToFile(slug);
  if (!file) return null;
  let html = await read(file);
  let fileSlug = slug;
  if (html === null && slug.length > 0) {
    // /docs/getting-started -> getting-started/index.html
    file = slugToFile([...slug, "index"]);
    if (!file) return null;
    html = await read(file);
    fileSlug = [...slug, "index"];
  }
  if (html === null) return null;

  const main = /<main[^>]*>([\s\S]*?)<\/main>/.exec(html)?.[1] ?? "";
  const h1 = /<h1[^>]*>([\s\S]*?)<\/h1>/.exec(main);
  const title = h1
    ? strip((h1[1] ?? "").replace(/<span class="draft">[\s\S]*?<\/span>/, ""))
    : "Docs";
  const draft = /<span class="draft">/.test(h1?.[1] ?? "");
  const meta = strip(/<div class='meta'>([\s\S]*?)<\/div>/.exec(main)?.[1] ?? "");
  const description = strip(
    /<\/div>\s*<p>([\s\S]*?)<\/p>/.exec(main)?.[1] ??
      /<\/h1>\s*<p>([\s\S]*?)<\/p>/.exec(main)?.[1] ??
      "",
  );

  const toc = [...main.matchAll(/<h2 id="([^"]+)">([\s\S]*?)<\/h2>/g)].map((m) => ({
    id: m[1] ?? "",
    text: strip(m[2] ?? ""),
  }));

  const pageDir = fileSlug.slice(0, -1);
  const body = main
    .replace(/<h1[^>]*>[\s\S]*?<\/h1>/, "")
    .replace(/<div class='meta'>[\s\S]*?<\/div>/, "")
    .replace(/href="([^"]*)"/g, (_m, h: string) => `href="${rewriteHref(h, pageDir)}"`);

  const first = slug[0];
  const section = first && (slug.length > 1 || dirNames.has(first)) ? first : null;
  return { slug, title, description, html: body, toc, draft, meta, section };
}

export async function getSections(): Promise<DocSection[]> {
  const html = (await read(path.join(ROOT, "index.html"))) ?? "";
  return [
    ...html.matchAll(
      /<a class="card" href="([^/]+)\/index\.html"><strong>([\s\S]*?)<\/strong><p>([\s\S]*?)<\/p><small>([\s\S]*?)<\/small>/g,
    ),
  ].map((m) => ({
    slug: m[1] ?? "",
    title: strip(m[2] ?? ""),
    description: strip(m[3] ?? ""),
    count: strip(m[4] ?? ""),
  }));
}

export async function getSectionLinks(section: string): Promise<DocLink[]> {
  const html = (await read(path.join(ROOT, section, "index.html"))) ?? "";
  const main = /<main[^>]*>([\s\S]*?)<\/main>/.exec(html)?.[1] ?? "";
  return [
    ...main.matchAll(/<li><a href="([^"]+)">([\s\S]*?)<\/a>(?: \u2014| —| ·)?\s*([\s\S]*?)<\/li>/g),
  ]
    .filter((m) => !(m[1] ?? "").startsWith("http"))
    .map((m) => ({
      href: `/docs/${section}/${(m[1] ?? "").replace(/\.html$/, "")}`,
      title: strip(m[2] ?? ""),
      summary: strip(m[3] ?? "").replace(/^[—·-]\s*/, ""),
    }));
}

export async function getSearchIndex(): Promise<
  { href: string; title: string; text: string; section: string }[]
> {
  const out: { href: string; title: string; text: string; section: string }[] = [];
  const sections = await getSections();
  const names = new Map(sections.map((s) => [s.slug, s.title]));
  async function walk(dir: string, rel: string[]) {
    for (const e of await fs.readdir(dir, { withFileTypes: true })) {
      if (e.isDirectory()) await walk(path.join(dir, e.name), [...rel, e.name]);
      else if (e.name.endsWith(".html") && e.name !== "404.html") {
        const base = e.name.replace(/\.html$/, "");
        const slug = base === "index" ? rel : [...rel, base];
        const doc = await getDoc(slug);
        if (!doc) continue;
        const text = strip(doc.html).slice(0, 700);
        out.push({
          href: `/docs${slug.length ? `/${slug.join("/")}` : ""}`,
          title: doc.title,
          text,
          section: names.get(rel[0] ?? "") ?? (rel[0] === "api" ? "API reference" : "Docs"),
        });
      }
    }
  }
  await walk(ROOT, []);
  return out;
}

export async function getAllSlugs(): Promise<string[][]> {
  const out: string[][] = [];
  async function walk(dir: string, rel: string[]) {
    for (const e of await fs.readdir(dir, { withFileTypes: true })) {
      if (e.isDirectory()) await walk(path.join(dir, e.name), [...rel, e.name]);
      else if (e.name.endsWith(".html") && e.name !== "404.html") {
        const base = e.name.replace(/\.html$/, "");
        out.push(base === "index" ? rel : [...rel, base]);
      }
    }
  }
  await walk(ROOT, []);
  return out;
}

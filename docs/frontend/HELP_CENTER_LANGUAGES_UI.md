# Request: languages in the help center (screens for the front end)

For the front-end developer. The API is done and tested; this is the screens. It builds on the
help center you already have (`Settings › Help center` and the public `/<workspace>/help`).
The app's own screens stay English: this is only about the **articles** a workspace shows its
customers.

**Depends on PR #79 in the CDESK repository** (help center languages, SEO, custom domain).
Until it is merged and deployed, the routes below answer 404. The exact fields are in
`docs/api/openapi.json` and the entry "2026-11-23: Help center: languages, SEO controls,
custom domain" in `docs/api/CHANGELOG.md` (both come with that PR). Regenerate
`src/lib/api/schema.d.ts` from the new `openapi.json`.

## How it works (read this first)

- An article is written in the workspace's **default language** (starts as `en`). Every other
  language is a **translation** kept beside it: title, body, status, and SEO text.
- A language is a code such as `hi` or `pt-br` (a 2 or 3 letter language, then at most one
  region or script part). The API stores it lower-case.
- A translation shows to customers only when **both** the article **and** the translation are
  published. A draft translation never hides the article: customers get the default text.
- Customers ask for a language with `?locale=hi`. The API answers with the best match: the exact
  code, then its base (`pt-br`, then `pt`), then the default language. It tells you which one
  you got (`locale`) and whether it was a fallback (`fallback: true`).
- Only **Admins** write (translations, default language, domain). Agents and light agents can
  read, drafts too.

## Part A: screens for Admins and agents

### A1. Default language and SEO switch (Settings › Help center)

- `GET /api/v1/help-center/settings` and `PUT` (Admin): `default_locale`, `seo_indexable`.
- A language picker for `default_locale`. Changing it answers **409** while any article already
  has a translation in the new default: show the API message and tell the Admin to delete or
  move those translations first.
- `seo_indexable` is a switch, **off** until an Admin turns it on. Say what it does: only then
  may search engines index the public help center (see B4).

### A2. Translations on an article

In the article editor add a **Languages** panel:

- List: `GET /api/v1/articles/{id}/translations`. Each row: language name, status (draft or
  published), last updated by.
- Add or edit: `PUT /api/v1/articles/{id}/translations/{locale}` with `title`, `body`, `status`
  (and the SEO fields below). A PUT makes or **replaces** the translation, so send all fields.
- Delete: `DELETE /api/v1/articles/{id}/translations/{locale}`.
- The default language is not a translation: `PUT` for it answers **422**, "edit the article".
  Do not offer it in the picker.
- At most **40** translations per article. A bad code answers **422** with a readable message.
- Body is Markdown, up to 100,000 characters. Title up to 200.
- `GET /api/v1/articles` rows carry `locales` (the languages that exist). Show them as small
  chips on the article list.
- Filter **"Needs a language"**: `GET /api/v1/articles?missing_locale=hi` returns the articles
  that still have no Hindi version.

Show language names with the browser, not a hand-made list:
`new Intl.DisplayNames([navigator.language], { type: "language" }).of("pt-br")`.
Offer a short list of common languages plus a free text box for any other valid code.

### A3. SEO fields (per article and per translation)

`slug`, `meta_title` (120), `meta_description` (320), `canonical_url` (https only). They are
sent on create, on article `PATCH`, and in the translation `PUT` (a PUT replaces them all).

- `suggested_slug` comes back from the API, made from the title. It is `null` when the title
  gives none (for example Hindi). Prefill the slug with it when it is not null, and let the
  Admin type one when it is.
- The slug is cosmetic. The page address is `/<workspace>/help/<id>/<slug>`; the **id** decides
  the article, so a wrong or changed slug breaks nothing. Make the route accept an optional slug
  segment (`[id]/[[...slug]]`).
- A translation has its **own** canonical address. It does not inherit the article's.

### A4. Category names in other languages

Categories carry `name_translations` (`{ "hi": "भुगतान" }`). Edit with
`PUT|DELETE /api/v1/article-categories/{id}/translations/{locale}`. Show them in the category
editor next to the article languages.

### A5. Custom domain (second phase, can ship later)

`GET|PUT|DELETE /api/v1/help-center/domain` and `POST /api/v1/help-center/domain/check`
(Admin). The flow: the Admin types `help.example.com`, the screen shows the DNS record to
publish (`dns` in the answer), the Admin presses **Check**, and the status goes pending then
verified. Show `status`, `last_error` and `failed_checks`. Off until the server has
`CEDESK_TLS_ASK_KEY`; if the routes answer 404 or 403, hide the section.

## Part B: the public help center (customers)

Files you already have: `src/components/public/help-center.tsx`, `src/hooks/use-portal.ts`,
`src/app/(public)/[workspace]/help/...`.

### B1. Ask for a language

- Pass `locale` to `GET /api/v1/public/{workspace}/articles`, `.../articles/{id}` and `.../vote`.
- Pick the visitor's language in this order: a `?locale=` in the address, then a cookie from
  an earlier choice, then `navigator.language`, then the workspace default.
- Decision for you and the team: a query parameter is the quickest. Search engines prefer a
  separate address per language (`/<workspace>/help/hi/...`). Start with the query parameter.

### B2. Language switcher

The article answer has `available_locales` (every published language, default included) and
`default_locale`. Show a switcher only when there is more than one. The list answer carries
`default_locale` and `locale` too.

### B3. Fallback notice and text direction

- When `fallback` is `true`, show a small line such as "This article is not available in your
  language yet. Showing English." (use the language name of `locale`).
- Set `lang={locale}` on the article container so screen readers and hyphenation use the right
  language.
- Right-to-left languages (`ar`, `he`, `fa`, `ur`) need `dir="rtl"` on the article container
  and the list. Use logical CSS (`margin-inline-start`, `text-align: start`) rather than
  `left` and `right` in the help center components.
- Fonts: make sure Devanagari, Arabic, Thai and CJK text does not fall back to a box glyph.

### B4. SEO tags and the sitemap (these are yours, the API only gives the data)

- `<title>` and `<meta name="description">` from `meta_title` and `meta_description`, falling
  back to the title and the start of the body.
- `<link rel="canonical">` from `canonical_url` when set, else the page's own address.
- `<link rel="alternate" hreflang="hi" href="...">` for every item of `alternates`
  (`[{ locale, slug }]`), plus `x-default` for the default language. Write the region in capitals
  in the tag (`pt-BR`): the API stores it lower-case.
- `GET /api/v1/public/{workspace}/sitemap` returns `{ indexable, entries, truncated }`. Build
  `sitemap.xml` from it, and `robots.txt` too. While `indexable` is `false`, send
  `<meta name="robots" content="noindex">` and an empty sitemap. It is cached for 5 minutes.

### B5. Votes

`POST .../vote` takes an optional `locale`: send the language of the page the visitor voted
on. A visitor still has one vote per article.

## What the API will not do (so the screens do not promise it)

These came out of testing with real text in 12 scripts:

- **Language codes:** only `language` or `language-region/script`. A three-part code such as
  `zh-hant-tw` is refused with 422 today. Do not offer it.
- **Search ignores case but not accents.** Searching `cafe` does not find "Café". Do not
  describe the search box as accent-insensitive.
- **Search matches the start of a word.** Scripts written without spaces (Thai, and probably
  Japanese and Chinese in the middle of a sentence) may not be found by a word from the middle.
- **Text typed in the decomposed Unicode form** (some Mac copy and paste) is stored as it came
  and can be missed by search. Normalise to NFC (`value.normalize("NFC")`) before you send a
  title or body.

These are known and may change on the API side. Tell us if one blocks you.

## Check it works

Use the sample text below in the editor and on the public pages.

| Language | Code | Title to try |
|----------|------|--------------|
| Hindi | `hi` | धनवापसी नीति |
| Arabic (right to left) | `ar` | سياسة الاسترداد |
| Hebrew (right to left) | `he` | מדיניות החזרים |
| Japanese | `ja` | 返金ポリシー |
| Chinese | `zh-hans` | 退款政策 |
| Korean | `ko` | 환불 정책 |
| Thai | `th` | นโยบายคืนเงิน |
| Russian | `ru` | Политика возврата |
| Turkish | `tr` | İade politikası |
| French with emoji | `fr` | Politique de remboursement ☕ 🎉 |
| Brazilian Portuguese | `pt-br` | Política de reembolso |

1. As Admin, add a Hindi translation as a **draft**. The public page in Hindi must still show
   English with the fallback line.
2. Publish it. The public page in Hindi shows the Hindi title and body, no fallback line, and
   the switcher lists English and Hindi.
3. Ask for `pt-BR` when only `pt` exists: the page shows Portuguese and says it is a fallback.
4. Ask for `fr` when there is no French: the page shows the default language.
5. Arabic and Hebrew pages read right to left, and the layout does not break.
6. Unpublish the **article**: every language disappears from the public pages (404).
7. Change the default language to one that already has a translation: you see the 409 message.
8. "Needs a language" lists only the articles without a Hindi version.
9. View the page source: `lang`, `hreflang` alternates and the canonical link are right, and
   the page says `noindex` until `seo_indexable` is switched on.
10. Sign in as an agent: the Languages panel is readable but every button that writes is hidden
    or refused (403).

## Notes

- Nothing here changes the API. If a field is not as described, tell us and we will fix the
  document or the API.
- This was written from the API's code and tests, not from your screens. It has not been
  tried against the real front end.

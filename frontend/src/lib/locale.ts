/**
 * Language codes for help center articles: `hi`, `pt-br`. A 2 or 3 letter language, then at
 * most one region or script part. The API stores them lower-case.
 */
const LOCALE = /^[a-z]{2,3}(-[a-z0-9]{2,8})?$/;

export function normalizeLocale(raw: string): string {
  return raw.trim().replace(/_/g, "-").toLowerCase();
}

export function isValidLocale(code: string): boolean {
  return LOCALE.test(code);
}

/** Languages written right to left. */
const RTL = new Set(["ar", "he", "fa", "ur"]);
export const isRtl = (locale: string | undefined | null): boolean =>
  !!locale && RTL.has(locale.toLowerCase().split("-")[0]!);

/** `pt-br` -> `pt-BR` for `hreflang` and `lang`; script parts (`zh-hans`) become `zh-Hans`. */
export function toBcp47(locale: string): string {
  const [lang, part] = locale.toLowerCase().split("-");
  if (!part) return lang!;
  if (part.length === 4) return `${lang}-${part[0]!.toUpperCase()}${part.slice(1)}`;
  return `${lang}-${part.toUpperCase()}`;
}

/** A short list to pick from; any other valid code can be typed. */
export const COMMON_LOCALES = [
  "en",
  "es",
  "fr",
  "de",
  "pt",
  "pt-br",
  "it",
  "nl",
  "hi",
  "ar",
  "he",
  "ja",
  "ko",
  "zh-hans",
  "zh-hant",
  "ru",
  "tr",
  "th",
  "pl",
  "id",
  "vi",
];

/** The language's name in the reader's language, from the browser. Falls back to the code. */
export function languageName(locale: string, inLocale?: string): string {
  try {
    const ui = inLocale ?? (typeof navigator === "undefined" ? "en" : navigator.language || "en");
    return new Intl.DisplayNames([ui], { type: "language" }).of(toBcp47(locale)) ?? locale;
  } catch {
    return locale;
  }
}

/** Replace decomposed Unicode (some Mac copy and paste) so the API's search can find it. */
export const nfc = (s: string): string => s.normalize("NFC");

/** Pick the visitor's language: `?locale=`, then the saved choice, then the browser. */
export const LOCALE_COOKIE = "cedesk.locale";

export function readLocaleCookie(): string {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(
    new RegExp(`(?:^|; )${LOCALE_COOKIE.replace(".", "\\.")}=([^;]*)`),
  );
  const v = m ? normalizeLocale(decodeURIComponent(m[1]!)) : "";
  return isValidLocale(v) ? v : "";
}

export function saveLocaleCookie(locale: string) {
  document.cookie = `${LOCALE_COOKIE}=${encodeURIComponent(locale)}; path=/; max-age=31536000; SameSite=Lax`;
}

export function browserLocale(): string {
  if (typeof navigator === "undefined") return "";
  const v = normalizeLocale(navigator.language || "");
  return isValidLocale(v) ? v : "";
}

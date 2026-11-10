import { getRequestConfig } from "next-intl/server";

/**
 * i18n without locale routing: one locale today, but every user-facing string
 * added from now on goes through `messages/<locale>.json`, so adding a language
 * is a translation job rather than a refactor. Resolve the locale here (cookie,
 * Accept-Language, or a member preference) when a second language ships.
 */
export default getRequestConfig(async () => {
  const locale = "en";
  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});

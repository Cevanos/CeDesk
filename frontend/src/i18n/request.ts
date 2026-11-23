import { getRequestConfig } from "next-intl/server";

/**
 * No locale routing, one locale for now. User-facing strings still go through
 * messages/<locale>.json so a second language is only translation work. Resolve
 * the locale here (cookie, Accept-Language, member pref) when that happens.
 */
export default getRequestConfig(async () => {
  const locale = "en";
  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});

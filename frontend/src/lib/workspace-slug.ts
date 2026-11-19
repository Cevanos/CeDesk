/** Rules for a workspace address (the backend checks them again). */
export const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$/;

/** Addresses the backend refuses. Mirrored here only for a faster message. */
export const RESERVED_SLUGS = new Set([
  "account",
  "agents",
  "api",
  "archive",
  "customers",
  "dev",
  "forgot-password",
  "login",
  "rate",
  "reports",
  "reset-password",
  "settings",
  "signup",
  "sso",
  "tickets",
  "sign-up",
  "register",
  "signin",
  "sign-in",
  "logout",
  "verify",
  "cedesk",
  "admin",
  "administrator",
  "app",
  "www",
  "mail",
  "email",
  "smtp",
  "support",
  "help",
  "helpdesk",
  "status",
  "docs",
  "blog",
  "billing",
  "static",
  "assets",
  "cdn",
  "portal",
  "security",
  "root",
  "system",
  "test",
  "demo",
]);

/** "Acme Support" gives "acme-support". */
export function suggestSlug(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
    .replace(/-+$/, "");
}

/** Lowercase as they type; spaces become dashes. */
export function cleanSlugInput(v: string): string {
  return v.toLowerCase().replace(/\s+/g, "-");
}

/** An error message, or null when the address is fine. */
export function slugProblem(s: string): string | null {
  if (!s) return "Enter a workspace address";
  if (s.length < 3 || s.length > 40) return "Use 3 to 40 characters";
  if (!SLUG_RE.test(s) || s.includes("--"))
    return "Use lowercase letters, digits and single dashes, not at the start or end";
  if (RESERVED_SLUGS.has(s)) return "That address is reserved. Try another.";
  return null;
}

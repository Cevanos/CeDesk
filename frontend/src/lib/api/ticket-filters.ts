import type { Priority, TicketStatus } from "@/lib/api/types";

export interface TicketFilters {
  status: TicketStatus;
  scope?: "mine" | "unassigned" | "everyone";
  priority?: Priority | "";
  team?: string;
  q?: string;
  /** "" = all tickets, "any" = imported from anywhere, or one import source. */
  imported?: string;
  sort?: "priority" | "newest" | "oldest" | "updated" | "sla";
}

const STATUS_VALUES: readonly TicketStatus[] = ["open", "pending", "resolved"];
const SCOPE_VALUES = ["mine", "unassigned", "everyone"] as const;
const PRIORITY_VALUES: readonly Priority[] = ["urgent", "high", "medium", "low"];
export const IMPORT_SOURCES = [
  ["intercom", "Intercom"],
  ["zendesk", "Zendesk"],
  ["freshdesk", "Freshdesk"],
  ["gorgias", "Gorgias"],
  ["helpscout", "Help Scout"],
  ["hubspot", "HubSpot"],
  ["zoho", "Zoho Desk"],
  ["csv", "CSV file"],
] as const;
const IMPORTED_VALUES = ["", "any", ...IMPORT_SOURCES.map(([v]) => v)] as const;
const SORT_VALUES = ["priority", "newest", "oldest", "updated", "sla"] as const;

function oneOf<T extends string>(allowed: readonly T[], value: string | null, fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}

/**
 * Reads filters from URL params, ignoring anything that is not a known value
 * so a hand-edited or stale link can never send junk to the API.
 */
export function filtersFromParams(sp: Pick<URLSearchParams, "get">): TicketFilters {
  return {
    status: oneOf(STATUS_VALUES, sp.get("status"), "open"),
    scope: oneOf(SCOPE_VALUES, sp.get("scope"), "everyone"),
    priority: oneOf<Priority | "">([...PRIORITY_VALUES, ""], sp.get("priority"), ""),
    team: sp.get("team") ?? "",
    q: sp.get("q") ?? "",
    imported: oneOf<string>(IMPORTED_VALUES, sp.get("imported"), ""),
    sort: oneOf(SORT_VALUES, sp.get("sort"), "priority"),
  };
}

/**
 * Remembers the last ticket-list query (filters, tab, sort) for this browser
 * tab, so opening a ticket and coming back restores the same view.
 * sessionStorage: per-tab, cleared when the tab closes, never shared.
 */
const KEY = "cdesk:ticket-list-query";

export function saveTicketListQuery(qs: string): void {
  try {
    if (qs) sessionStorage.setItem(KEY, qs);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage unavailable — filters just won't persist */
  }
}

export function loadTicketListQuery(): string {
  try {
    return sessionStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

/** `/tickets` plus the remembered query, for links that go back to the list. */
export function ticketListHref(): string {
  const qs = loadTicketListQuery();
  return qs ? `/tickets?${qs}` : "/tickets";
}

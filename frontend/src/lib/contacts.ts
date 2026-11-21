import type { Query } from "@/lib/api/client";
import type {
  AttributeValue,
  ContactFieldRef,
  ContactFilters,
  ContactSort,
  SegmentFilters,
} from "@/lib/api/contacts-types";

export const EMPTY_FILTERS: ContactFilters = {
  q: "",
  sort: "name",
  vip: false,
  has_open: false,
  created_from: "",
  created_to: "",
  attributes: {},
};

export const SORT_LABEL: Record<ContactSort, string> = {
  name: "Name A–Z",
  recent: "Recent activity",
  tickets: "Most tickets",
};

/** The list's query string values. `attr` repeats: `attr=plan:pro&attr=trial:true`. */
export function filtersToQuery(f: ContactFilters): Query {
  const attr = Object.entries(f.attributes).map(([k, v]) => `${k}:${v}`);
  return {
    q: f.q.trim() || undefined,
    sort: f.sort === "name" ? undefined : f.sort,
    vip: f.vip ? true : undefined,
    has_open: f.has_open ? true : undefined,
    created_from: f.created_from || undefined,
    created_to: f.created_to || undefined,
    attr: attr.length ? attr : undefined,
  };
}

/** What narrows the list (sort alone does not count). */
export function isFiltered(f: ContactFilters): boolean {
  return !!(
    f.q.trim() ||
    f.vip ||
    f.has_open ||
    f.created_from ||
    f.created_to ||
    Object.keys(f.attributes).length
  );
}

/** A chip's text value typed for its field: numbers and checkboxes are not strings. */
export function typedValue(raw: string, field?: Pick<ContactFieldRef, "kind">): AttributeValue {
  if (field?.kind === "number") {
    const n = Number(raw);
    return raw.trim() !== "" && Number.isFinite(n) ? n : raw;
  }
  if (field?.kind === "checkbox") return raw === "true";
  return raw;
}

/** Only what is set, in the shape `POST /segments` takes. */
export function filtersToSegment(f: ContactFilters, fields: ContactFieldRef[]): SegmentFilters {
  const out: SegmentFilters = {};
  if (f.q.trim()) out.q = f.q.trim();
  if (f.sort !== "name") out.sort = f.sort;
  if (f.vip) out.vip = true;
  if (f.has_open) out.has_open = true;
  if (f.created_from) out.created_from = f.created_from;
  if (f.created_to) out.created_to = f.created_to;
  const keys = Object.keys(f.attributes);
  if (keys.length) {
    out.attributes = Object.fromEntries(
      keys.map((k) => [
        k,
        typedValue(
          f.attributes[k] ?? "",
          fields.find((x) => x.key === k),
        ),
      ]),
    );
  }
  return out;
}

export function segmentToFilters(s: SegmentFilters): ContactFilters {
  return {
    q: s.q ?? "",
    sort: s.sort ?? "name",
    vip: !!s.vip,
    has_open: !!s.has_open,
    created_from: s.created_from ?? "",
    created_to: s.created_to ?? "",
    attributes: Object.fromEntries(
      Object.entries(s.attributes ?? {}).map(([k, v]) => [k, String(v ?? "")]),
    ),
  };
}

/** Short labels for a segment's filters, shown as read-only chips. */
export function describeFilters(s: SegmentFilters, fields: ContactFieldRef[] = []): string[] {
  const out: string[] = [];
  if (s.q) out.push(`Search: ${s.q}`);
  if (s.vip) out.push("VIP only");
  if (s.has_open) out.push("Has open tickets");
  if (s.created_from) out.push(`Created from ${s.created_from}`);
  if (s.created_to) out.push(`Created to ${s.created_to}`);
  for (const [k, v] of Object.entries(s.attributes ?? {})) {
    const label = fields.find((f) => f.key === k)?.label ?? k;
    out.push(`${label}: ${String(v)}`);
  }
  return out;
}

/** A slug for a field key from its label: `Plan tier` becomes `plan_tier`. */
export function keyFromLabel(label: string): string {
  const k = label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return /^[a-z]/.test(k) ? k.slice(0, 40) : k ? `f_${k}`.slice(0, 40) : "";
}

/** How an attribute value reads on screen. */
export function showValue(v: AttributeValue | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  return String(v);
}

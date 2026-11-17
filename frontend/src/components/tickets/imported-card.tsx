import { ExternalLink } from "lucide-react";

import type { Ticket, TicketType } from "@/lib/api/types";

const SOURCE_LABEL: Record<string, string> = {
  zendesk: "Zendesk",
  intercom: "Intercom",
  freshdesk: "Freshdesk",
  gorgias: "Gorgias",
  helpscout: "Help Scout",
  hubspot: "HubSpot",
  zoho: "Zoho Desk",
  csv: "CSV file",
};

const show = (v: unknown): string => {
  if (v === null || v === undefined || v === "") return "";
  if (Array.isArray(v)) return v.map(show).filter(Boolean).join(", ");
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
};

/** The fields an import kept that the ticket's type doesn't define, as read-only rows. */
export function importedRows(ticket: Ticket, type?: TicketType | null) {
  const defined = new Set((type?.fields ?? []).map((f) => f.key));
  const rows: { key: string; label: string; value: string }[] = [];
  for (const [key, raw] of Object.entries(ticket.fields ?? {})) {
    if (key === "source_url" || defined.has(key)) continue;
    const value = show(raw);
    if (!value) continue;
    rows.push({
      key,
      label: key === "more_tags" ? "More tags" : key.replace(/_/g, " "),
      value,
    });
  }
  return rows;
}

export function isImported(ticket: Ticket) {
  const fields = ticket.fields ?? {};
  return (
    (!!ticket.source && ticket.source in SOURCE_LABEL) ||
    Object.keys(fields).some((k) => k.startsWith("source_"))
  );
}

/** Read-only "From <source>" card. Nothing here is sent back in a PATCH. */
export function ImportedCard({ ticket, type }: { ticket: Ticket; type?: TicketType | null }) {
  if (!isImported(ticket)) return null;
  const name = SOURCE_LABEL[ticket.source ?? ""] ?? "the old help desk";
  const url = ticket.fields?.source_url;
  const safeUrl = typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
  const rows = importedRows(ticket, type);
  if (!safeUrl && rows.length === 0) return null;
  return (
    <>
      <h3 className="mb-4 flex items-center gap-2 text-[11px] font-bold tracking-[0.08em] text-muted-foreground uppercase after:h-px after:flex-1 after:bg-border">
        From {name}
      </h3>
      {safeUrl && (
        <a
          href={safeUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="mb-3 inline-flex items-center gap-1.5 text-sm font-medium text-primary-ink hover:underline"
        >
          Open in {name} <ExternalLink className="size-3.5" aria-hidden />
        </a>
      )}
      {rows.length > 0 && (
        <dl className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-sm">
          {rows.map((r) => (
            <div key={r.key} className="contents">
              <dt className="text-muted-foreground capitalize">{r.label}</dt>
              <dd className="text-right break-words">{r.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}

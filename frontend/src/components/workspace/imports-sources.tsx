"use client";

import { FileSpreadsheet } from "lucide-react";
import { SiHelpscout, SiHubspot, SiIntercom, SiZendesk, SiZoho } from "react-icons/si";

import type { ImportKind } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export interface FieldDef {
  key: string;
  label: string;
  hint?: string;
  options?: string[];
  optional?: boolean;
}
export interface SourceDef {
  id: string;
  label: string;
  settings: FieldDef[];
  credentials: FieldDef[];
  help: string;
  /** Alternative ways to sign in (Zendesk). The first is the default and replaces `credentials`. */
  authModes?: { id: string; label: string; credentials: FieldDef[]; help?: string }[];
}

export const SOURCES: SourceDef[] = [
  {
    id: "intercom",
    label: "Intercom",
    settings: [{ key: "region", label: "Region", options: ["us", "eu", "au"] }],
    credentials: [{ key: "token", label: "Access token" }],
    help: "An access token with permission to read contacts, conversations and articles.",
  },
  {
    id: "zendesk",
    label: "Zendesk",
    settings: [{ key: "subdomain", label: "Subdomain", hint: "acme for acme.zendesk.com" }],
    credentials: [
      { key: "client_id", label: "Client ID" },
      { key: "client_secret", label: "Client secret" },
    ],
    help: "Connect with an OAuth client (recommended) or an agent email and API token. Zendesk retires API tokens on 30 Apr 2027.",
    authModes: [
      {
        id: "oauth",
        label: "OAuth client (recommended)",
        credentials: [
          { key: "client_id", label: "Client ID" },
          { key: "client_secret", label: "Client secret" },
        ],
        help: "Admin Center › Apps and integrations › APIs › OAuth clients › Add OAuth client. Kind: Confidential, scope: read.",
      },
      {
        id: "token",
        label: "API token",
        credentials: [
          { key: "email", label: "Agent email" },
          { key: "token", label: "API token" },
        ],
        help: "An agent email and an API token from Admin Center.",
      },
    ],
  },
  {
    id: "freshdesk",
    label: "Freshdesk",
    settings: [{ key: "domain", label: "Domain", hint: "acme for acme.freshdesk.com" }],
    credentials: [{ key: "api_key", label: "API key" }],
    help: "The API key from your Freshdesk profile settings.",
  },
  {
    id: "gorgias",
    label: "Gorgias",
    settings: [{ key: "domain", label: "Domain", hint: "acme for acme.gorgias.com" }],
    credentials: [
      { key: "email", label: "Email" },
      { key: "api_key", label: "API key" },
    ],
    help: "Your login email and a REST API key.",
  },
  {
    id: "helpscout",
    label: "Help Scout",
    settings: [],
    credentials: [
      { key: "app_id", label: "App ID" },
      { key: "app_secret", label: "App secret" },
      { key: "docs_key", label: "Docs API key (help center)", optional: true },
    ],
    help: "An OAuth app's ID and secret. Add a Docs key to import the help center too.",
  },
  {
    id: "hubspot",
    label: "HubSpot",
    settings: [],
    credentials: [{ key: "token", label: "Private app token or service key" }],
    help: "Required scopes: crm.objects.tickets.read, crm.objects.contacts.read, crm.objects.owners.read, sales-email-read, files.read. Optional: calls, meetings and conversations.read (chat threads). HubSpot's knowledge base isn't imported.",
  },
  {
    id: "zoho",
    label: "Zoho Desk",
    settings: [
      { key: "region", label: "Region", options: ["us", "eu", "in", "au", "jp"] },
      {
        key: "org_id",
        label: "Organization ID",
        optional: true,
        hint: "Only if an error asks for it",
      },
    ],
    credentials: [
      { key: "client_id", label: "Client ID" },
      { key: "client_secret", label: "Client secret" },
      { key: "refresh_token", label: "Refresh token" },
    ],
    help: "A self client's ID, secret and a refresh token. Scopes: Desk.tickets.READ, Desk.contacts.READ, Desk.basic.READ, Desk.articles.READ (plus Desk.search.READ for archived tickets).",
  },
];

export const KINDS: { id: ImportKind; label: string }[] = [
  { id: "contacts", label: "Contacts" },
  { id: "tickets", label: "Tickets" },
  { id: "articles", label: "Help articles" },
  { id: "replies", label: "Saved replies" },
];

export const label = (s: string) =>
  SOURCES.find((x) => x.id === s)?.label ?? (s === "csv" ? "CSV file" : s);

const BRAND: Record<string, { color: string; Icon: React.ElementType }> = {
  intercom: { color: "#1F8DED", Icon: SiIntercom },
  zendesk: { color: "#03363D", Icon: SiZendesk },
  freshdesk: { color: "#25C16F", Icon: FreshdeskMark },
  gorgias: { color: "#E8505B", Icon: GorgiasMark },
  helpscout: { color: "#1292EE", Icon: SiHelpscout },
  hubspot: { color: "#FF7A59", Icon: SiHubspot },
  zoho: { color: "#E42527", Icon: SiZoho },
};

/** Not in the simple-icons set, so drawn here as simple marks in the brand colour. */
function FreshdeskMark(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <path d="M12 2a9 9 0 0 0-9 9v5a3 3 0 0 0 3 3h1v-7H5v-1a7 7 0 1 1 14 0v1h-2v7h1a3 3 0 0 0 3-3v-5a9 9 0 0 0-9-9Zm-1 17h2a2 2 0 0 1 2 2v1h-6v-1a2 2 0 0 1 2-2Z" />
    </svg>
  );
}
function GorgiasMark(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <path d="M12 2C6.5 2 2 6.2 2 11.5S6.5 21 12 21c1.5 0 2.9-.3 4.2-.9L21 22l-1.6-4.2A9.2 9.2 0 0 0 22 11.5C22 6.2 17.5 2 12 2Zm.2 4.5c1.6 0 3 .6 4 1.6l-1.5 1.6c-.7-.6-1.5-.9-2.5-.9-2 0-3.5 1.5-3.5 3.4s1.500 3.4 3.500 3.400c1.300 0 2.200-.5 2.700-1.300h-2.800v-2.100h5.200c.1.400.1.700.1 1.100 0 3-2.200 5.200-5.200 5.200S7 15.200 7 11.900s2.300-5.400 5.200-5.400Z" />
    </svg>
  );
}

export function SourceIcon({ id, className }: { id: string; className?: string }) {
  const b = BRAND[id];
  const Icon = b?.Icon ?? FileSpreadsheet;
  return (
    <span
      aria-hidden
      className={cn(
        "grid size-10 flex-none place-items-center rounded-xl border border-border bg-white",
        className,
      )}
    >
      <Icon className="size-[55%]" style={{ color: b?.color ?? "#16A34A" }} />
    </span>
  );
}

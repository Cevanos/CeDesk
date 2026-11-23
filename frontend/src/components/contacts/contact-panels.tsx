"use client";

import { AttributesCard } from "@/components/contacts/attributes-card";
import { NotesCard } from "@/components/contacts/notes-card";
import { useContact } from "@/hooks/use-contacts";

/**
 * Attributes and notes for the ticket's Customer tab. Shares the customer's cache entry
 * with the customer page. Shows nothing if the customer can't be loaded.
 */
export function ContactPanels({ contactId }: { contactId: string }) {
  const q = useContact(contactId);
  if (!q.data) return null;
  return (
    <>
      <AttributesCard contact={q.data} className="rounded-2xl" />
      <NotesCard contact={q.data} compact />
    </>
  );
}

"use client";

import { MessageCircle } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button, Card, Field, Input } from "@/components/ui/primitives";
import { useTicketFromConversation } from "@/hooks/use-tickets";
import { ApiError } from "@/lib/api/client";

/** "Make a ticket from a Cevanos chat" (guide 5.15). */
export function FromChatCard() {
  const router = useRouter();
  const make = useTicketFromConversation();
  const [id, setId] = useState("");

  return (
    <Card className="rounded-2xl border-dashed p-4 sm:p-5">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          const conv = id.trim();
          if (!conv) return;
          make.mutate(conv, {
            onSuccess: (t) => {
              toast.success(`Ticket ${t.label} created from the chat`);
              router.push(`/tickets/${t.id}`);
            },
          });
        }}
      >
        <div className="min-w-0 flex-1 basis-64">
          <Field
            label="Start from a Cevanos chat"
            htmlFor="conv-id"
            hint="Paste the chat's conversation ID."
            error={
              make.error instanceof ApiError && make.error.status !== 503
                ? make.error.userMessage
                : undefined
            }
          >
            <Input
              id="conv-id"
              value={id}
              onChange={(e) => setId(e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          </Field>
        </div>
        <Button type="submit" variant="outline" loading={make.isPending} disabled={!id.trim()}>
          <MessageCircle className="size-4" aria-hidden /> Create ticket
        </Button>
      </form>
    </Card>
  );
}

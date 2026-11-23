"use client";

import { Loader2, Wand2 } from "lucide-react";
import { toast } from "sonner";

import { MenuItem, Popover } from "@/components/ui/overlay";
import { Button } from "@/components/ui/primitives";
import { useMacros, useRunMacro } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { Macro } from "@/lib/api/types";

/** Steps shown to the agent before running, e.g. "Reply · Resolve". */
export function describeSteps(m: Macro): string {
  return m.steps
    .map((s) => {
      switch (s.kind) {
        case "reply":
          return "Reply";
        case "note":
          return "Note";
        case "set_status":
          return `Status: ${String(s.status)}`;
        case "set_priority":
          return `Priority: ${String(s.priority)}`;
        case "set_team":
          return "Re-route";
        case "add_tag":
          return `Tag: ${String(s.tag)}`;
        default:
          return String(s.kind ?? "step");
      }
    })
    .join(" · ");
}

/**
 * Macros menu on the ticket. The steps run in one go; if the
 * server refuses one, nothing is saved. Render only for agents who can reply.
 */
export function MacrosMenu({ ticketId, etag }: { ticketId: string; etag: string | null }) {
  const macros = useMacros();
  const run = useRunMacro(ticketId);

  return (
    <Popover
      trigger={({ props }) => (
        <Button variant="outline" {...props} loading={run.isPending}>
          {!run.isPending && <Wand2 className="size-4" aria-hidden />} Macros
        </Button>
      )}
    >
      {(close) => (
        <div className="max-h-80 w-72 overflow-y-auto">
          {macros.isPending ? (
            <p className="flex items-center gap-2 px-2.5 py-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden /> Loading…
            </p>
          ) : macros.isError ? (
            <p className="px-2.5 py-2 text-sm text-danger">
              {macros.error instanceof ApiError ? macros.error.userMessage : "Couldn't load macros"}
            </p>
          ) : macros.data.length === 0 ? (
            <p className="px-2.5 py-2 text-sm text-muted-foreground">
              No macros yet. An admin can add them in Settings › Macros.
            </p>
          ) : (
            macros.data.map((m) => (
              <MenuItem
                key={m.id}
                disabled={run.isPending || m.problems.length > 0}
                title={m.problems.join(" ") || undefined}
                className="flex-col items-start gap-0.5"
                onClick={() => {
                  close();
                  run.mutate(
                    { macroId: m.id, etag },
                    { onSuccess: () => toast.success(`Ran “${m.name}”`) },
                  );
                }}
              >
                <span className="font-medium text-foreground">{m.name}</span>
                <span className="text-xs text-muted-foreground">{describeSteps(m)}</span>
              </MenuItem>
            ))
          )}
        </div>
      )}
    </Popover>
  );
}

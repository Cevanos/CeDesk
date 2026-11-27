"use client";

import { Plug, Unplug } from "lucide-react";
import { toast } from "sonner";

import { Section } from "@/components/account/section";
import { useConfirm } from "@/components/ui/confirm";
import { Button, Skeleton } from "@/components/ui/primitives";
import { LoadError } from "@/components/workspace/list-shared";
import { useConnectedApps, useDisconnectApp } from "@/hooks/workspace/connected-apps";
import { ApiError } from "@/lib/api/client";

function when(iso?: string | null) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString();
}

/** The AI apps this member connected to the workspace, each with Disconnect. */
export function ConnectedAppsCard() {
  const apps = useConnectedApps();
  const disconnect = useDisconnectApp();
  const { ask, dialog } = useConfirm();

  return (
    <Section
      icon={<Plug className="size-5" />}
      title="Connected apps"
      description="AI apps such as Claude and ChatGPT that you let act as you in this workspace."
    >
      {apps.isPending ? (
        <Skeleton className="h-16" />
      ) : apps.isError ? (
        <LoadError error={apps.error} />
      ) : apps.data.length === 0 ? (
        <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-muted-foreground">
          No apps connected. Add CeDesk to an AI app with your CeDesk&apos;s MCP address, then
          approve it when it asks.
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {apps.data.map((g) => {
            const used = when(g.last_used_at);
            return (
              <li key={g.id} className="flex flex-wrap items-center gap-3 px-3 py-2.5">
                <div className="min-w-0 flex-1 basis-56">
                  <div className="truncate text-sm font-semibold">
                    {g.app.name}
                    {g.app.domain && g.app.domain !== g.app.name && (
                      <span className="ml-2 text-xs font-normal text-muted-foreground">
                        {g.app.domain}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {g.scopes.includes("write") ? "Can read and make changes" : "Can read only"}
                    {" · "}Connected {when(g.created_at)}
                    {" · "}
                    {used ? `Last used ${used}` : "Not used yet"}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={disconnect.isPending}
                  onClick={() =>
                    ask({
                      title: "Disconnect this app?",
                      message: `${g.app.name} stops working with CeDesk at once. You can connect it again later.`,
                      confirmLabel: "Disconnect",
                      run: () =>
                        disconnect.mutate(g.id, {
                          onSuccess: () => toast.success(`${g.app.name} disconnected`),
                          onError: (e) =>
                            toast.error(
                              e instanceof ApiError ? e.userMessage : "Couldn't disconnect",
                            ),
                        }),
                    })
                  }
                >
                  <Unplug className="size-3.5" /> Disconnect
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      {dialog}
    </Section>
  );
}

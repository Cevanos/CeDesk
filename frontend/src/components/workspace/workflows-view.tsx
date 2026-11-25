"use client";

import {
  FlaskConical,
  History,
  ListChecks,
  Lock,
  MoreHorizontal,
  Pencil,
  Plus,
  Trash2,
  Workflow,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { MenuItem, Popover } from "@/components/ui/overlay";
import { Badge, Button, Card, EmptyState, Skeleton } from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { FlowTestDrawer } from "@/components/workspace/automation-test";
import { LoadError, Problems } from "@/components/workspace/list-shared";
import { ReplayDialog } from "@/components/workspace/replay-dialog";
import { EVENT_LABEL, WEBHOOK_LOCKED } from "@/components/workspace/rules-view";
import {
  flowCallsWebhook,
  useAutomationAccess,
  useAutomationCatalog,
  useDeleteFlow,
  useFlows,
  useToggleFlow,
} from "@/hooks/use-workspace";
import type { Flow } from "@/lib/api/automation-types";

const menuLink =
  "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-foreground-2 hover:bg-surface-2";

const waitingText = (n: number) => `${n} waiting`;

export function WorkflowsView() {
  const flows = useFlows();
  const catalog = useAutomationCatalog();
  const toggle = useToggleFlow();
  const del = useDeleteFlow();
  const access = useAutomationAccess();
  const { ask, dialog } = useConfirm();
  const [testing, setTesting] = useState<Flow | null>(null);
  const [replaying, setReplaying] = useState<Flow | null>(null);

  const triggerLabel = (e: string) =>
    catalog.data?.triggers.find((t) => t.event === e)?.label ??
    EVENT_LABEL[e as keyof typeof EVENT_LABEL] ??
    e;

  const setEnabled = (f: Flow, enabled: boolean) => {
    const run = () =>
      toggle.mutate(
        { id: f.id, enabled },
        { onSuccess: () => toast.success(enabled ? "Workflow turned on" : "Workflow turned off") },
      );
    if (enabled || f.waiting_runs === 0) return run();
    ask({
      title: "Turn this workflow off?",
      message: `Its ${f.waiting_runs} waiting ${f.waiting_runs === 1 ? "run" : "runs"} will stop.`,
      detail: f.name,
      confirmLabel: "Turn off",
      tone: "warning",
      run,
    });
  };

  return (
    <div>
      {dialog}
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          A workflow is a chain of steps that can wait. It runs on its trigger, right after the
          rules, and goes from step to step until a wait or an end. A waiting run goes on by itself
          when the time passes or, if you choose that, when the customer replies. Each change it
          makes writes a line on the ticket.
        </p>
        <Link href="/settings/workflows/new">
          <Button>
            <Plus className="size-4" /> New workflow
          </Button>
        </Link>
      </div>

      {flows.isPending ? (
        <Skeleton className="h-32" />
      ) : flows.isError ? (
        <LoadError error={flows.error} />
      ) : flows.data.length === 0 ? (
        <Card>
          <EmptyState icon={<Workflow className="size-6" />} title="No workflows yet">
            For example: when a VIP writes in, tag the ticket, wait a day for them to reply, then
            raise the priority.
          </EmptyState>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-border">
            {flows.data.map((f) => {
              const locked = flowCallsWebhook(f.graph) && !access.manageAccess;
              return (
                <li key={f.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="min-w-0 flex-1 basis-56">
                      <div className="flex flex-wrap items-center gap-2">
                        <Link
                          href={`/settings/workflows/${f.id}`}
                          className="font-medium hover:underline"
                        >
                          {f.name}
                        </Link>
                        <Badge tone={f.enabled ? "success" : "neutral"}>
                          {f.enabled ? "On" : "Off"}
                        </Badge>
                        {f.waiting_runs > 0 && (
                          <Badge tone="info">{waitingText(f.waiting_runs)}</Badge>
                        )}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {triggerLabel(f.trigger)} · Version {f.version}
                      </div>
                      {locked && (
                        <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                          <Lock className="size-3" aria-hidden /> {WEBHOOK_LOCKED}
                        </p>
                      )}
                    </div>
                    <Switch
                      label={`${f.name} ${f.enabled ? "on" : "off"}`}
                      checked={f.enabled}
                      disabled={toggle.isPending || locked}
                      onChange={(v) => setEnabled(f, v)}
                    />
                    <Popover
                      className="w-56"
                      trigger={({ props }) => (
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`More actions for ${f.name}`}
                          {...props}
                        >
                          <MoreHorizontal className="size-4" />
                        </Button>
                      )}
                    >
                      {(close) => (
                        <>
                          <Link
                            role="menuitem"
                            href={`/settings/workflows/${f.id}`}
                            onClick={close}
                            className={menuLink}
                          >
                            <Pencil className="size-4" aria-hidden /> {locked ? "View" : "Edit"}
                          </Link>
                          <MenuItem
                            onClick={() => {
                              close();
                              setTesting(f);
                            }}
                          >
                            <FlaskConical className="size-4" aria-hidden /> Test
                          </MenuItem>
                          <MenuItem
                            onClick={() => {
                              close();
                              setReplaying(f);
                            }}
                          >
                            <History className="size-4" aria-hidden /> Replay on past tickets
                          </MenuItem>
                          <Link
                            role="menuitem"
                            href={`/settings/workflows/${f.id}?versions=1`}
                            onClick={close}
                            className={menuLink}
                          >
                            <History className="size-4" aria-hidden /> Versions
                          </Link>
                          <Link
                            role="menuitem"
                            href={`/settings/automation/log?flow_id=${f.id}`}
                            onClick={close}
                            className={menuLink}
                          >
                            <ListChecks className="size-4" aria-hidden /> Run log
                          </Link>
                          <MenuItem
                            disabled={locked}
                            className="text-danger"
                            onClick={() => {
                              close();
                              ask({
                                title: "Delete this workflow?",
                                message:
                                  f.waiting_runs > 0
                                    ? `Its ${f.waiting_runs} waiting ${f.waiting_runs === 1 ? "run" : "runs"} will stop. This can't be undone.`
                                    : "This can't be undone.",
                                detail: f.name,
                                confirmLabel: "Delete",
                                run: () =>
                                  del.mutate(f.id, {
                                    onSuccess: () => toast.success("Workflow deleted"),
                                  }),
                              });
                            }}
                          >
                            <Trash2 className="size-4" aria-hidden /> Delete
                          </MenuItem>
                        </>
                      )}
                    </Popover>
                  </div>
                  <Problems items={f.problems} />
                </li>
              );
            })}
          </ul>
        </Card>
      )}

      {replaying && (
        <ReplayDialog
          title={replaying.name}
          target={{ kind: "flow", flowId: replaying.id }}
          onClose={() => setReplaying(null)}
        />
      )}
      {testing && (
        <FlowTestDrawer
          open
          onClose={() => setTesting(null)}
          title={testing.name}
          flowId={testing.id}
        />
      )}
    </div>
  );
}

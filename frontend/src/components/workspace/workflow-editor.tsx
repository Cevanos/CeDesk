"use client";

import { ArrowLeft, FlaskConical, History, Lock, Save } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Dialog } from "@/components/ui/overlay";
import { Badge, Button, Card, Field, Input, Skeleton } from "@/components/ui/primitives";
import {
  FlowTestDrawer,
  FlowTestResultView,
  formatWhen,
} from "@/components/workspace/automation-test";
import { LoadError } from "@/components/workspace/list-shared";
import { WEBHOOK_LOCKED } from "@/components/workspace/rules-view";
import { WorkflowCanvas } from "@/components/workspace/workflow-canvas";
import { useDebouncedValue } from "@/hooks/use-global-search";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import {
  flowCallsWebhook,
  useAutomationAccess,
  useFlow,
  useFlowValidation,
  useFlowVersions,
  useSaveFlow,
} from "@/hooks/use-workspace";
import type { Flow, FlowGraph, FlowTestResult, FlowVersion } from "@/lib/api/automation-types";
import { ApiError } from "@/lib/api/client";
import type { TicketSummary } from "@/lib/api/types";
import { fromCanvas, graphKey, startGraph, toCanvas } from "@/lib/automation/graph";

const DEFAULT_PER_HOUR = 1000;
const DEFAULT_PER_DAY = 10000;

/** Positions and labels don't matter to the live check, so moving a node does not re-run it. */
const shapeOf = (g: FlowGraph): FlowGraph => ({
  nodes: g.nodes.map((n) => {
    const copy = { ...n };
    delete copy.position;
    delete copy.label;
    return copy;
  }),
  edges: g.edges,
});

/** What the canvas would hand back for this graph, so "unchanged" compares like with like. */
const normalise = (g: FlowGraph): FlowGraph => {
  const c = toCanvas(g);
  return fromCanvas(c.nodes, c.edges);
};

export function WorkflowEditor({
  id,
  openVersions = false,
}: {
  id?: string;
  openVersions?: boolean;
}) {
  const flow = useFlow(id ?? null);
  if (id && flow.isPending) return <Skeleton className="h-80" />;
  if (id && flow.isError) return <LoadError error={flow.error} />;
  return (
    <EditorBody key={flow.data?.id ?? "new"} flow={flow.data ?? null} openVersions={openVersions} />
  );
}

function EditorBody({ flow, openVersions }: { flow: Flow | null; openVersions: boolean }) {
  const router = useRouter();
  const access = useAutomationAccess();
  const save = useSaveFlow();

  const [name, setName] = useState(flow?.name ?? "");
  const [perHour, setPerHour] = useState(flow?.max_runs_per_hour?.toString() ?? "");
  const [perDay, setPerDay] = useState(flow?.max_runs_per_day?.toString() ?? "");
  const [startFrom, setStartFrom] = useState<FlowGraph>(() => flow?.graph ?? startGraph());
  const [graph, setGraph] = useState<FlowGraph>(startFrom);
  const [editKey, setEditKey] = useState(0);
  const [viewing, setViewing] = useState<FlowVersion | null>(null);
  const [versionsOpen, setVersionsOpen] = useState(openVersions);
  const [testOpen, setTestOpen] = useState(false);
  const [test, setTest] = useState<{ result: FlowTestResult; ticket: TicketSummary } | null>(null);
  const [errors, setErrors] = useState<{ name?: string; limits?: string; form?: string }>({});
  const lastKey = useRef("");

  const locked = !!flow && flowCallsWebhook(flow.graph) && !access.manageAccess;
  const readOnly = locked || !!viewing;

  const baseline = useMemo(() => (flow ? graphKey(normalise(flow.graph)) : null), [flow]);
  const dirty =
    !readOnly &&
    (flow
      ? name !== flow.name ||
        graphKey(graph) !== baseline ||
        perHour !== (flow.max_runs_per_hour?.toString() ?? "") ||
        perDay !== (flow.max_runs_per_day?.toString() ?? "")
      : true);
  useUnsavedChanges(dirty && (!!flow || name !== "" || graph.nodes.length > 1));

  // The live check (shape only), a moment after the last change.
  const shape = useDebouncedValue(readOnly ? null : shapeOf(graph), 500);
  const check = useFlowValidation(shape);
  const shapeError = !readOnly && check.data && !check.data.valid ? check.data.error : null;

  const nodeName = (nodeId: string) => graph.nodes.find((n) => n.id === nodeId)?.label || nodeId;

  const onGraph = (g: FlowGraph) => {
    const key = graphKey(g);
    if (key === lastKey.current) return;
    lastKey.current = key;
    setGraph(g);
    setTest(null); // the path no longer matches what is drawn
  };

  const limit = (text: string, min: number, max: number): number | undefined | "bad" => {
    if (text.trim() === "") return undefined;
    const n = Number(text);
    return Number.isInteger(n) && n >= min && n <= max ? n : "bad";
  };

  const submit = () => {
    const next: typeof errors = {};
    const trimmed = name.trim();
    if (trimmed.length < 1 || trimmed.length > 80) next.name = "Enter a name of 1 to 80 characters";
    const hour = limit(perHour, 1, 100_000);
    const day = limit(perDay, 1, 1_000_000);
    if (hour === "bad") next.limits = "The hourly limit is a whole number from 1 to 100000.";
    else if (day === "bad") next.limits = "The daily limit is a whole number from 1 to 1000000.";
    else if ((day ?? DEFAULT_PER_DAY) < (hour ?? DEFAULT_PER_HOUR))
      next.limits = "The daily limit can't be lower than the hourly one.";
    setErrors(next);
    if (Object.keys(next).length) return;

    const limits = {
      ...(typeof hour === "number" ? { max_runs_per_hour: hour } : {}),
      ...(typeof day === "number" ? { max_runs_per_day: day } : {}),
    };
    // A new workflow sends everything; a change sends only what is different.
    const body = flow
      ? {
          id: flow.id,
          ...(trimmed !== flow.name ? { name: trimmed } : {}),
          ...(graphKey(graph) !== baseline ? { graph } : {}),
          ...(perHour !== (flow.max_runs_per_hour?.toString() ?? "") ||
          perDay !== (flow.max_runs_per_day?.toString() ?? "")
            ? limits
            : {}),
        }
      : { name: trimmed, graph, ...limits };

    save.mutate(body, {
      onSuccess: (saved) => {
        setErrors({});
        if (flow) {
          toast.success("Saved");
        } else {
          toast.success("Saved. Turn it on when you are ready.");
          router.replace(`/settings/workflows/${saved.id}`);
        }
      },
      onError: (e) => {
        if (e instanceof ApiError && e.status === 409) {
          setErrors({ name: e.message || "Another workflow already has that name." });
        } else if (e instanceof ApiError && e.status === 422) {
          setErrors({ form: e.details.map((d) => d.message).join(" ") || e.message });
        } else {
          setErrors({ form: e instanceof ApiError ? e.userMessage : "Could not save." });
        }
      },
    });
  };

  const loadVersion = (v: FlowVersion) => {
    setStartFrom(v.graph);
    setGraph(v.graph);
    lastKey.current = "";
    setEditKey((k) => k + 1);
    setViewing(null);
    setTest(null);
    toast.info(`Version ${v.version} is loaded. Save to make it a new version.`);
  };
  const backToEditing = () => {
    setStartFrom(graph);
    setEditKey((k) => k + 1);
    setViewing(null);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link
          href="/settings/workflows"
          className="inline-flex items-center gap-1 text-sm font-medium text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" aria-hidden /> Workflows
        </Link>
        {flow && (
          <>
            <Badge tone={flow.enabled ? "success" : "neutral"}>{flow.enabled ? "On" : "Off"}</Badge>
            <Badge tone="neutral">Version {flow.version}</Badge>
          </>
        )}
      </div>

      <Card className="space-y-4 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Name" htmlFor="wf-name" error={errors.name} className="min-w-56 flex-1">
            <Input
              id="wf-name"
              value={name}
              maxLength={80}
              disabled={readOnly}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <div className="flex flex-wrap gap-2">
            {flow && (
              <Button variant="outline" onClick={() => setVersionsOpen(true)}>
                <History className="size-4" /> Versions
              </Button>
            )}
            <Button variant="outline" disabled={!!viewing} onClick={() => setTestOpen(true)}>
              <FlaskConical className="size-4" /> Test
            </Button>
            {!readOnly && (
              <Button loading={save.isPending} onClick={submit}>
                <Save className="size-4" /> Save
              </Button>
            )}
          </div>
        </div>

        {locked && (
          <p
            role="note"
            className="flex items-center gap-2 rounded-lg bg-surface-2 px-3 py-2 text-sm text-muted-foreground"
          >
            <Lock className="size-4 flex-none" aria-hidden /> {WEBHOOK_LOCKED}
          </p>
        )}
        {viewing && (
          <div
            role="note"
            className="flex flex-wrap items-center gap-2 rounded-lg bg-info-soft px-3 py-2 text-sm text-info"
          >
            <span>
              Viewing version {viewing.version} (read only), saved {formatWhen(viewing.created_at)}
              {viewing.created_by_name ? ` by ${viewing.created_by_name}` : ""}.
            </span>
            <span className="ml-auto flex gap-2">
              <Button size="sm" variant="outline" onClick={backToEditing}>
                Back to my version
              </Button>
              {!locked && (
                <Button size="sm" onClick={() => loadVersion(viewing)}>
                  Load into editor
                </Button>
              )}
            </span>
          </div>
        )}
        {(shapeError || errors.form || errors.limits) && (
          <p
            role="alert"
            className="rounded-lg border border-danger/25 bg-danger-soft px-3 py-2 text-sm text-danger"
          >
            {shapeError ?? errors.form ?? errors.limits}
          </p>
        )}

        <WorkflowCanvas
          key={viewing ? `v${viewing.version}` : `e${editKey}`}
          initial={viewing ? viewing.graph : startFrom}
          readOnly={readOnly}
          path={viewing ? null : (test?.result.steps ?? null)}
          canCallWebhook={access.manageAccess}
          onChange={viewing ? undefined : onGraph}
        />

        {test && !viewing && (
          <section
            aria-label="Test result"
            className="space-y-2 rounded-xl border border-border p-3"
          >
            <h3 className="text-sm font-semibold">
              Test on {test.ticket.label}{" "}
              <span className="font-normal text-muted-foreground">
                · Test only: nothing on the ticket changes
              </span>
            </h3>
            <FlowTestResultView result={test.result} ticket={test.ticket} nodeName={nodeName} />
          </section>
        )}

        <details className="rounded-lg border border-border px-3 py-2">
          <summary className="cursor-pointer text-[13px] font-medium text-foreground-2">
            Advanced
          </summary>
          <div className="mt-3 space-y-3" inert={readOnly}>
            <p className="text-xs text-muted-foreground">
              Stops a workflow that runs far more than you expect, for example after a mistake in
              its conditions.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field
                label="At most … runs an hour"
                htmlFor="wf-hour"
                hint={`Default ${DEFAULT_PER_HOUR}`}
              >
                <Input
                  id="wf-hour"
                  type="number"
                  min={1}
                  max={100000}
                  value={perHour}
                  placeholder={String(DEFAULT_PER_HOUR)}
                  onChange={(e) => setPerHour(e.target.value)}
                />
              </Field>
              <Field
                label="… runs a day"
                htmlFor="wf-day"
                hint={`Default ${DEFAULT_PER_DAY}. Not lower than the hourly limit.`}
              >
                <Input
                  id="wf-day"
                  type="number"
                  min={1}
                  max={1000000}
                  value={perDay}
                  placeholder={String(DEFAULT_PER_DAY)}
                  onChange={(e) => setPerDay(e.target.value)}
                />
              </Field>
            </div>
          </div>
        </details>
        {!flow && (
          <p className="text-xs text-muted-foreground">
            A new workflow starts turned off. Turn it on from the list when you are ready.
          </p>
        )}
      </Card>

      <FlowTestDrawer
        open={testOpen}
        onClose={() => setTestOpen(false)}
        title={name.trim() || "Workflow test"}
        graph={graph}
        onResult={(result, ticket) => setTest({ result, ticket })}
      />

      {flow && versionsOpen && (
        <VersionsDialog
          flowId={flow.id}
          current={flow.version}
          onClose={() => setVersionsOpen(false)}
          onView={(v) => {
            setViewing(v);
            setVersionsOpen(false);
          }}
        />
      )}
    </div>
  );
}

function VersionsDialog({
  flowId,
  current,
  onClose,
  onView,
}: {
  flowId: string;
  current: number;
  onClose: () => void;
  onView: (v: FlowVersion) => void;
}) {
  const versions = useFlowVersions(flowId);
  return (
    <Dialog
      open
      onClose={onClose}
      title="Versions"
      description="Open one to see its graph. To go back to it, load it into the editor and save."
    >
      {versions.isPending ? (
        <Skeleton className="h-24" />
      ) : versions.isError ? (
        <LoadError error={versions.error} />
      ) : (
        <ul className="divide-y divide-border">
          {versions.data.map((v) => (
            <li key={v.version}>
              <button
                type="button"
                onClick={() => onView(v)}
                className="flex w-full items-center gap-2 px-1 py-2.5 text-left text-sm hover:bg-surface-2"
              >
                <span className="font-medium">Version {v.version}</span>
                {v.version === current && <Badge tone="primary">Current</Badge>}
                <span className="ml-auto text-xs text-muted-foreground">
                  {formatWhen(v.created_at)}
                  {v.created_by_name ? ` · ${v.created_by_name}` : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}

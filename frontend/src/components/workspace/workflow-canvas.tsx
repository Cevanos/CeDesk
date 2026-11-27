"use client";

import "@xyflow/react/dist/style.css";

import {
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  type Connection,
  type Edge,
  type EdgeChange,
  type NodeChange,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
} from "@xyflow/react";
import { CircleHelp, Clock, GitFork, Play, Plus, Trash2, Zap } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useMemo, useState } from "react";

import { Button, Input, Select } from "@/components/ui/primitives";
import { DurationInput } from "@/components/workspace/automation-fields";
import { outcomeLabel } from "@/components/workspace/automation-test";
import {
  AUTOMATION_ACTION_KINDS,
  AUTOMATION_CONDITION_KINDS,
  automationActionKinds,
  automationConditionKinds,
  ItemBuilder,
  summarize,
  type Item,
} from "@/components/workspace/item-builder";
import { EVENT_LABEL } from "@/components/workspace/rules-view";
import { useContactFields } from "@/hooks/use-contacts";
import { useAutomationCatalog } from "@/hooks/use-workspace";
import type {
  AutoItem,
  FlowGraph,
  FlowNode,
  FlowTestStep,
  NodeType,
  Way,
} from "@/lib/api/automation-types";
import {
  type CanvasNode,
  describeNode,
  fromCanvas,
  newNode,
  nextNodeId,
  pruneEdges,
  toCanvas,
  WAY_LABEL,
  waysOf,
} from "@/lib/automation/graph";
import { cn } from "@/lib/utils";

const FALLBACK_LIMITS = { nodes: 50, conditions: 10, waitMinutes: 43_200 };

const ITEM_KINDS = [...AUTOMATION_CONDITION_KINDS, ...AUTOMATION_ACTION_KINDS];

const TYPE_META: Record<NodeType, { label: string; icon: typeof Zap }> = {
  trigger: { label: "Trigger", icon: Play },
  condition: { label: "Condition", icon: GitFork },
  action: { label: "Action", icon: Zap },
  wait: { label: "Wait", icon: Clock },
};

/* ---------- node cards ---------- */

function NodeCard({ id, data, selected }: NodeProps<CanvasNode>) {
  const node = data.node;
  const meta = TYPE_META[node.type];
  const Icon = meta.icon;
  const catalog = useAutomationCatalog();
  const contactFields = useContactFields().data;
  const itemLabel = (i: AutoItem) => summarize(i as Item, ITEM_KINDS, contactFields);
  const ways = waysOf(node);
  const triggerLabel = (e: string) =>
    catalog.data?.triggers.find((t) => t.event === e)?.label ??
    EVENT_LABEL[e as keyof typeof EVENT_LABEL] ??
    e;
  const text = node.type === "trigger" ? triggerLabel(node.event) : describeNode(node, itemLabel);

  return (
    <div
      className={cn(
        "relative w-56 rounded-xl border bg-surface px-3 py-2.5 shadow-card transition",
        selected ? "border-primary ring-4 ring-primary/15" : "border-border",
        data.outcome && "border-primary",
        data.dim && "opacity-40",
      )}
    >
      {node.type !== "trigger" && (
        <Handle type="target" position={Position.Top} aria-label={`Into ${node.id}`} />
      )}
      <div className="flex items-center gap-2 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
        <Icon className="size-3.5" aria-hidden />
        {meta.label}
        {data.outcome && (
          <span className="ml-auto rounded-full bg-primary-soft px-2 py-0.5 text-[10px] text-primary-ink normal-case">
            {outcomeLabel(data.outcome)}
          </span>
        )}
      </div>
      {node.label && <p className="mt-1 truncate text-sm font-semibold">{node.label}</p>}
      <p className="mt-0.5 line-clamp-3 text-xs break-words text-foreground-2">{text}</p>
      {ways.map((w, i) => (
        <Handle
          key={w}
          type="source"
          id={w}
          position={Position.Bottom}
          aria-label={`${WAY_LABEL[w]} output of ${id}`}
          style={ways.length > 1 ? { left: `${((i + 1) * 100) / (ways.length + 1)}%` } : undefined}
        />
      ))}
      {ways.length > 1 && (
        <div className="mt-2 flex justify-around text-[10px] font-medium text-muted-foreground">
          {ways.map((w) => (
            <span key={w}>{WAY_LABEL[w]}</span>
          ))}
        </div>
      )}
    </div>
  );
}

const nodeTypes = {
  trigger: NodeCard,
  condition: NodeCard,
  action: NodeCard,
  wait: NodeCard,
};

/* ---------- the settings of one node ---------- */

function NodePanel({
  node,
  triggerEvent,
  triggers,
  canCallWebhook,
  maxConditions,
  maxWait,
  onChange,
  onDelete,
}: {
  node: FlowNode;
  triggerEvent: string;
  triggers: { value: string; label: string }[];
  canCallWebhook: boolean;
  maxConditions: number;
  maxWait: number;
  onChange: (n: FlowNode) => void;
  onDelete: () => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{TYPE_META[node.type].label}</h3>
        {node.type !== "trigger" && (
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Delete ${TYPE_META[node.type].label.toLowerCase()} ${node.id}`}
            onClick={onDelete}
          >
            <Trash2 className="size-3.5" /> Delete
          </Button>
        )}
      </div>

      <div className="space-y-1.5">
        <label htmlFor="node-label" className="text-[13px] font-medium text-foreground-2">
          Label (optional)
        </label>
        <Input
          id="node-label"
          value={node.label ?? ""}
          maxLength={80}
          onChange={(e) => onChange({ ...node, label: e.target.value || undefined })}
        />
      </div>

      {node.type === "trigger" && (
        <div className="space-y-1.5">
          <span className="text-[13px] font-medium text-foreground-2">Runs when</span>
          <Select
            aria-label="Trigger event"
            value={node.event}
            options={triggers}
            onChange={(e) => onChange({ ...node, event: e.target.value })}
          />
        </div>
      )}

      {node.type === "condition" && (
        <>
          <ItemBuilder
            title="Conditions (all must match)"
            addLabel="Add condition"
            kinds={automationConditionKinds(triggerEvent)}
            items={node.conditions as Item[]}
            minItems={1}
            maxItems={maxConditions}
            onChange={(conditions) => onChange({ ...node, conditions })}
          />
          <p className="text-xs text-muted-foreground">
            Two outputs: Yes when all match, No when they do not.
          </p>
        </>
      )}

      {node.type === "action" && (
        <ItemBuilder
          title="Action"
          addLabel="Add action"
          kinds={automationActionKinds(canCallWebhook)}
          items={[node.action as Item]}
          minItems={1}
          maxItems={1}
          onChange={(items) => items[0] && onChange({ ...node, action: items[0] })}
        />
      )}

      {node.type === "wait" && (
        <>
          <div className="space-y-1.5">
            <span className="text-[13px] font-medium text-foreground-2">Wait for</span>
            <DurationInput
              label="Wait for"
              minutes={node.minutes}
              min={1}
              max={maxWait}
              onChange={(minutes) => onChange({ ...node, minutes })}
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-[var(--primary)]"
              checked={node.until === "customer_replied"}
              onChange={(e) =>
                onChange({ ...node, until: e.target.checked ? "customer_replied" : undefined })
              }
            />
            Or until the customer replies
          </label>
          <p className="text-xs text-muted-foreground">
            {node.until
              ? "Two outputs: Replied, and Timed out."
              : "One output, used when the time has passed."}
          </p>
        </>
      )}
    </div>
  );
}

/* ---------- the canvas ---------- */

interface Props {
  /** The graph to start from. Give the component a new `key` to load another one. */
  initial: FlowGraph;
  readOnly?: boolean;
  /** Test mode: the steps a test run took. Nodes it did not reach are greyed. */
  path?: FlowTestStep[] | null;
  canCallWebhook: boolean;
  onChange?: (graph: FlowGraph) => void;
}

export function WorkflowCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}

function CanvasInner({ initial, readOnly = false, path, canCallWebhook, onChange }: Props) {
  const { resolvedTheme } = useTheme();
  const catalog = useAutomationCatalog();
  const start = useMemo(() => toCanvas(initial), [initial]);
  const [nodes, setNodes] = useState<CanvasNode[]>(start.nodes);
  const [edges, setEdges] = useState<Edge[]>(start.edges);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const limits = {
    nodes: catalog.data?.limits?.nodes ?? FALLBACK_LIMITS.nodes,
    conditions: catalog.data?.limits?.conditions_per_step ?? FALLBACK_LIMITS.conditions,
    waitMinutes: catalog.data?.limits?.wait_minutes ?? FALLBACK_LIMITS.waitMinutes,
  };
  const triggers = useMemo(
    () =>
      catalog.data?.triggers.length
        ? catalog.data.triggers.map((t) => ({ value: t.event, label: t.label }))
        : Object.entries(EVENT_LABEL).map(([value, label]) => ({ value, label })),
    [catalog.data],
  );

  // Tell the editor about every change (the first call is the starting graph).
  useEffect(() => {
    onChange?.(fromCanvas(nodes, edges));
    // `onChange` is stable enough: the editor only reacts when the graph really differs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges]);

  const onNodesChange = (changes: NodeChange<CanvasNode>[]) => {
    if (readOnly) {
      // Looking around is fine; nothing may change.
      changes = changes.filter((c) => c.type === "select" || c.type === "dimensions");
    }
    // The trigger can't be deleted.
    changes = changes.filter(
      (c) =>
        !(c.type === "remove" && nodes.find((n) => n.id === c.id)?.data.node.type === "trigger"),
    );
    setNodes((ns) => applyNodeChanges(changes, ns));
    const removed = changes.filter((c) => c.type === "remove").map((c) => c.id);
    if (removed.length) {
      setEdges((es) =>
        es.filter((e) => !removed.includes(e.source) && !removed.includes(e.target)),
      );
      setSelectedId((s) => (s && removed.includes(s) ? null : s));
    }
  };
  const onEdgesChange = (changes: EdgeChange[]) => {
    if (readOnly) return;
    setEdges((es) => applyEdgeChanges(changes, es));
  };

  const onConnect = (c: Connection) => {
    if (readOnly || !c.source || !c.target) return;
    const way = (c.sourceHandle ?? "next") as Way;
    // Each output has at most one edge: a new one replaces the old.
    setEdges((es) => [
      ...es.filter((e) => !(e.source === c.source && (e.sourceHandle ?? "next") === way)),
      {
        id: `${c.source}:${way}`,
        source: c.source,
        target: c.target,
        sourceHandle: way,
        label: way === "next" ? undefined : WAY_LABEL[way],
      },
    ]);
  };
  const isValidConnection = (c: Connection | Edge) =>
    c.source !== c.target && nodes.find((n) => n.id === c.target)?.data.node.type !== "trigger";

  const add = (type: Exclude<NodeType, "trigger">) => {
    if (nodes.length >= limits.nodes) return;
    const id = nextNodeId(
      type,
      nodes.map((n) => n.id),
    );
    const lowest = nodes.reduce((m, n) => Math.max(m, n.position.y), 0);
    const from = nodes.find((n) => n.id === selectedId) ?? nodes[0];
    const position = { x: from?.position.x ?? 0, y: lowest + 140 };
    const node = newNode(type, id, position);
    setNodes((ns) => [
      ...ns.map((n) => ({ ...n, selected: false })),
      { id, type, position, data: { node }, selected: true, deletable: true },
    ]);
    setSelectedId(id);
  };

  const update = (n: FlowNode) => {
    const next = nodes.map((x) => (x.id === n.id ? { ...x, data: { ...x.data, node: n } } : x));
    setNodes(next);
    setEdges((es) => pruneEdges(next, es));
  };
  const remove = (id: string) => onNodesChange([{ id, type: "remove" }]);

  // Test mode: show the path.
  const outcomes = useMemo(() => new Map((path ?? []).map((s) => [s.node, s.outcome])), [path]);
  const shown = useMemo(
    () =>
      path
        ? nodes.map((n) => {
            const reached = n.data.node.type === "trigger" || outcomes.has(n.id);
            return {
              ...n,
              data: { ...n.data, outcome: outcomes.get(n.id), dim: !reached },
            };
          })
        : nodes,
    [nodes, outcomes, path],
  );

  const selected = nodes.find((n) => n.id === selectedId)?.data.node ?? null;
  const triggerEvent =
    (nodes.find((n) => n.data.node.type === "trigger")?.data.node as { event?: string } | undefined)
      ?.event ?? "created";
  const full = nodes.length >= limits.nodes;

  return (
    <div className="grid gap-3 lg:grid-cols-[10rem_minmax(0,1fr)_20rem]">
      <div className="flex flex-wrap gap-2 lg:flex-col" role="group" aria-label="Add a step">
        <p className="hidden text-[11px] font-bold tracking-[0.08em] text-subtle-foreground uppercase lg:block">
          Steps
        </p>
        <p className="flex items-center gap-1.5 rounded-lg bg-surface-2 px-2.5 py-1.5 text-xs text-muted-foreground">
          <Play className="size-3.5" aria-hidden /> Trigger is always there
        </p>
        {(["condition", "action", "wait"] as const).map((t) => {
          const Icon = TYPE_META[t].icon;
          return (
            <Button
              key={t}
              size="sm"
              variant="outline"
              disabled={readOnly || full}
              onClick={() => add(t)}
              className="justify-start"
            >
              <Plus className="size-3.5" aria-hidden />
              <Icon className="size-3.5" aria-hidden />
              {TYPE_META[t].label}
            </Button>
          );
        })}
        {full && <p className="text-xs text-muted-foreground">At most {limits.nodes} steps.</p>}
        <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
          <CircleHelp className="mt-0.5 size-3.5 flex-none" aria-hidden />
          Drag from a dot under a step to another step. Each output has one line.
        </p>
      </div>

      <div className="h-[60vh] min-h-[420px] overflow-hidden rounded-xl border border-border bg-surface-2">
        <ReactFlow<CanvasNode>
          nodes={shown}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          isValidConnection={isValidConnection}
          onNodeClick={(_, n) => setSelectedId(n.id)}
          onPaneClick={() => setSelectedId(null)}
          nodesDraggable={!readOnly}
          nodesConnectable={!readOnly}
          deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]}
          colorMode={resolvedTheme === "dark" ? "dark" : "light"}
          fitView
          fitViewOptions={{ maxZoom: 1, padding: 0.3 }}
          proOptions={{ hideAttribution: true }}
          aria-label="Workflow canvas"
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      <aside
        aria-label="Step settings"
        className="rounded-xl border border-border bg-surface p-3 lg:max-h-[60vh] lg:overflow-y-auto"
      >
        {selected ? (
          <div inert={readOnly}>
            <NodePanel
              key={selected.id}
              node={selected}
              triggerEvent={triggerEvent}
              triggers={triggers}
              canCallWebhook={canCallWebhook}
              maxConditions={limits.conditions}
              maxWait={limits.waitMinutes}
              onChange={update}
              onDelete={() => remove(selected.id)}
            />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            Click a step to see its settings. {readOnly ? "This view is read only." : ""}
          </p>
        )}
      </aside>
    </div>
  );
}

import type { Edge, Node } from "@xyflow/react";

import type {
  AutoItem,
  FlowEdge,
  FlowGraph,
  FlowNode,
  NodeType,
  Way,
} from "@/lib/api/automation-types";

/** What an RF node carries: the workflow node itself, plus what test mode says about it. */
export interface CanvasData extends Record<string, unknown> {
  node: FlowNode;
  /** Test mode: the outcome at this node. Undefined when the run did not reach it. */
  outcome?: string;
  /** Test mode is showing a path: nodes off the path are greyed. */
  dim?: boolean;
}
export type CanvasNode = Node<CanvasData>;

export const WAY_LABEL: Record<Way, string> = {
  next: "Next",
  yes: "Yes",
  no: "No",
  replied: "Replied",
  timeout: "Timed out",
};

/** The outputs a node has. An output with no edge simply ends the run. */
export function waysOf(node: FlowNode): Way[] {
  switch (node.type) {
    case "condition":
      return ["yes", "no"];
    case "wait":
      return node.until ? ["replied", "timeout"] : ["next"];
    default:
      return ["next"];
  }
}

/** Which way an edge without `when` means. */
export function defaultWay(from: FlowNode | undefined): Way {
  return from?.type === "wait" && from.until ? "timeout" : "next";
}

export const edgeId = (from: string, way: Way) => `${from}:${way}`;

export function toCanvas(graph: FlowGraph): { nodes: CanvasNode[]; edges: Edge[] } {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const nodes: CanvasNode[] = graph.nodes.map((n, i) => ({
    id: n.id,
    type: n.type,
    position: n.position ?? { x: 0, y: i * 130 },
    data: { node: n },
    deletable: n.type !== "trigger",
  }));
  const edges: Edge[] = [];
  const seen = new Set<string>();
  for (const e of graph.edges) {
    const way = e.when ?? defaultWay(byId.get(e.from));
    const id = edgeId(e.from, way);
    if (seen.has(id)) continue; // one edge per output
    seen.add(id);
    edges.push({
      id,
      source: e.from,
      target: e.to,
      sourceHandle: way,
      label: way === "next" ? undefined : WAY_LABEL[way],
    });
  }
  return { nodes, edges };
}

export function fromCanvas(nodes: CanvasNode[], edges: Edge[]): FlowGraph {
  return {
    nodes: nodes.map((n) => ({
      ...n.data.node,
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
    })),
    edges: edges.map((e): FlowEdge => ({
      from: e.source,
      to: e.target,
      when: (e.sourceHandle as Way | null | undefined) ?? "next",
    })),
  };
}

/** Drops edges whose output no longer exists (e.g. after "or until the customer replies" is unticked). */
export function pruneEdges(nodes: CanvasNode[], edges: Edge[]): Edge[] {
  const ways = new Map(nodes.map((n) => [n.id, waysOf(n.data.node)]));
  return edges.filter((e) => ways.get(e.source)?.includes((e.sourceHandle ?? "next") as Way));
}

/** A free id: type_1, type_2, ... (1-40 of a-z 0-9 _ -). */
export function nextNodeId(type: NodeType, taken: Iterable<string>): string {
  const used = new Set(taken);
  for (let i = 1; ; i++) {
    const id = `${type}_${i}`;
    if (!used.has(id)) return id;
  }
}

export function newNode(
  type: Exclude<NodeType, "trigger">,
  id: string,
  position: { x: number; y: number },
): FlowNode {
  switch (type) {
    case "condition":
      return { id, type, position, conditions: [{ kind: "contact_vip", value: true }] };
    case "action":
      return { id, type, position, action: { kind: "add_tag", tag: "" } };
    case "wait":
      return { id, type, position, minutes: 60 };
  }
}

export function startGraph(event = "created"): FlowGraph {
  return {
    nodes: [{ id: "start", type: "trigger", event, position: { x: 0, y: 0 } }],
    edges: [],
  };
}

/** Stable text for comparing two graphs (positions included: moving a node is a change). */
export const graphKey = (g: FlowGraph) => JSON.stringify(g);

/* ---------- durations (the API speaks minutes) ---------- */

export type DurationUnit = "minutes" | "hours" | "days";
const UNIT_MINUTES: Record<DurationUnit, number> = { minutes: 1, hours: 60, days: 1440 };

/** The largest unit that divides `minutes` exactly, so 2880 shows as 2 days. */
export function splitMinutes(minutes: number): { value: number; unit: DurationUnit } {
  if (minutes > 0 && minutes % 1440 === 0) return { value: minutes / 1440, unit: "days" };
  if (minutes > 0 && minutes % 60 === 0) return { value: minutes / 60, unit: "hours" };
  return { value: minutes, unit: "minutes" };
}

export const toMinutes = (value: number, unit: DurationUnit) =>
  Math.round(value * UNIT_MINUTES[unit]);

export function formatMinutes(minutes: number): string {
  const { value, unit } = splitMinutes(minutes);
  const word = value === 1 ? unit.slice(0, -1) : unit;
  return `${value} ${word}`;
}

/** One line for a node card. */
export function describeNode(node: FlowNode, itemLabel: (i: AutoItem) => string): string {
  switch (node.type) {
    case "trigger":
      return node.event;
    case "condition":
      return node.conditions.map(itemLabel).join(" and ") || "No conditions yet";
    case "action":
      return itemLabel(node.action);
    case "wait":
      return `Wait ${formatMinutes(node.minutes)}${node.until ? " or until the customer replies" : ""}`;
  }
}

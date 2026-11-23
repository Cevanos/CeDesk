import { describe, expect, it } from "vitest";

import type { FlowGraph } from "@/lib/api/automation-types";
import {
  formatMinutes,
  fromCanvas,
  nextNodeId,
  pruneEdges,
  splitMinutes,
  toCanvas,
  toMinutes,
  waysOf,
} from "@/lib/automation/graph";

const graph: FlowGraph = {
  nodes: [
    { id: "start", type: "trigger", event: "created", position: { x: 0, y: 0 } },
    { id: "vip", type: "condition", conditions: [{ kind: "contact_vip", value: true }] },
    { id: "wait", type: "wait", minutes: 1440, until: "customer_replied" },
    { id: "tag", type: "action", action: { kind: "add_tag", tag: "vip" } },
  ],
  edges: [
    { from: "start", to: "vip", when: "next" },
    { from: "vip", to: "wait", when: "yes" },
    { from: "wait", to: "tag" }, // no `when` on a wait with `until` means timeout
  ],
};

describe("graph <-> canvas", () => {
  it("round-trips and always writes `when`", () => {
    const { nodes, edges } = toCanvas(graph);
    const back = fromCanvas(nodes, edges);
    expect(back.edges).toEqual([
      { from: "start", to: "vip", when: "next" },
      { from: "vip", to: "wait", when: "yes" },
      { from: "wait", to: "tag", when: "timeout" },
    ]);
    expect(back.nodes.map((n) => n.id)).toEqual(["start", "vip", "wait", "tag"]);
    expect(back.nodes[0]?.position).toEqual({ x: 0, y: 0 });
  });

  it("keeps a single edge per output", () => {
    const g: FlowGraph = {
      ...graph,
      edges: [
        { from: "vip", to: "wait", when: "yes" },
        { from: "vip", to: "tag", when: "yes" },
      ],
    };
    expect(toCanvas(g).edges).toHaveLength(1);
  });

  it("drops edges from outputs that are gone", () => {
    const { nodes, edges } = toCanvas(graph);
    const plain = nodes.map((n) =>
      n.id === "wait"
        ? { ...n, data: { node: { id: "wait", type: "wait" as const, minutes: 5 } } }
        : n,
    );
    expect(pruneEdges(plain, edges).map((e) => e.id)).toEqual(["start:next", "vip:yes"]);
  });
});

describe("helpers", () => {
  it("lists the outputs per node type", () => {
    expect(waysOf({ id: "a", type: "condition", conditions: [] })).toEqual(["yes", "no"]);
    expect(waysOf({ id: "a", type: "wait", minutes: 1, until: "customer_replied" })).toEqual([
      "replied",
      "timeout",
    ]);
    expect(waysOf({ id: "a", type: "wait", minutes: 1 })).toEqual(["next"]);
  });

  it("picks free node ids", () => {
    expect(nextNodeId("action", ["action_1", "action_2"])).toBe("action_3");
    expect(nextNodeId("wait", [])).toBe("wait_1");
  });

  it("shows durations in the largest exact unit", () => {
    expect(splitMinutes(2880)).toEqual({ value: 2, unit: "days" });
    expect(splitMinutes(120)).toEqual({ value: 2, unit: "hours" });
    expect(splitMinutes(90)).toEqual({ value: 90, unit: "minutes" });
    expect(toMinutes(2, "days")).toBe(2880);
    expect(formatMinutes(1440)).toBe("1 day");
    expect(formatMinutes(60 * 5)).toBe("5 hours");
  });
});

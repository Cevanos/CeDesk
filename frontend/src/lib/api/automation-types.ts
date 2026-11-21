/** Phase 5: automation. Rule test mode, workflows (flows), the catalog and the run log. */

/** A condition or action: `{kind, ...params}`. */
export type AutoItem = Record<string, unknown> & { kind: string };

export type Way = "next" | "yes" | "no" | "replied" | "timeout";
export type NodeType = "trigger" | "condition" | "action" | "wait";

export interface GraphPosition {
  x: number;
  y: number;
}

interface NodeBase {
  id: string;
  position?: GraphPosition;
  label?: string;
}
export interface TriggerNode extends NodeBase {
  type: "trigger";
  event: string;
}
export interface ConditionNode extends NodeBase {
  type: "condition";
  conditions: AutoItem[];
}
export interface ActionNode extends NodeBase {
  type: "action";
  action: AutoItem;
}
export interface WaitNode extends NodeBase {
  type: "wait";
  minutes: number;
  /** Left out for a plain wait. */
  until?: "customer_replied";
}
export type FlowNode = TriggerNode | ConditionNode | ActionNode | WaitNode;

export interface FlowEdge {
  from: string;
  to: string;
  when?: Way;
}
export interface FlowGraph {
  nodes: FlowNode[];
  edges: FlowEdge[];
}

export interface Flow {
  id: string;
  name: string;
  enabled: boolean;
  trigger: string;
  version: number;
  graph: FlowGraph;
  max_runs_per_hour: number;
  max_runs_per_day: number;
  waiting_runs: number;
  problems: string[];
  created_by?: string | null;
  created_by_name?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface FlowVersion {
  version: number;
  graph: FlowGraph;
  created_at: string;
  created_by_name?: string | null;
}

export interface FlowValidation {
  valid: boolean;
  trigger: string | null;
  error: string | null;
}

/** GET /automation/catalog. `params` is a JSON schema per kind. */
export interface CatalogEntry {
  kind: string;
  label: string;
  params?: { properties?: Record<string, { enum?: unknown[] } & Record<string, unknown>> };
  note?: string;
}
export interface AutomationCatalog {
  triggers: { event: string; label: string }[];
  conditions: CatalogEntry[];
  actions: CatalogEntry[];
  nodes?: { type: string; ways: Way[] }[];
  limits?: {
    nodes?: number;
    edges?: number;
    conditions_per_step?: number;
    wait_minutes?: number;
    waits_per_run?: number;
    steps_per_run?: number;
    flows?: number;
    rules?: number;
  };
}

/** The ticket as it would be after a test run. */
export interface TicketAfter {
  status: string;
  priority: string;
  type_id: string | null;
  team_id: string | null;
  tags: string[];
  snoozed: boolean;
  followers: string[];
}

export interface RuleTestResult {
  rule_name: string;
  event: string;
  matched: boolean;
  conditions: { condition: AutoItem; matched: boolean }[];
  actions: { action: AutoItem; would: string | null }[];
  blocked: string | null;
  ticket_after: TicketAfter;
}

export interface FlowTestStep {
  node: string;
  type: NodeType;
  outcome: string;
  said: string | null;
}
export interface FlowTestResult {
  trigger: string;
  steps: FlowTestStep[];
  would_wait: {
    node: string;
    minutes: number;
    until_reply: boolean;
    resume_at: string;
  } | null;
  stopped: string | null;
  blocked: string | null;
  ticket_after: TicketAfter;
}

export type RunState = "done" | "waiting" | "blocked" | "cancelled" | "failed";

export interface AutomationRun {
  id: string;
  kind: "rule" | "flow";
  rule_id: string | null;
  flow_id: string | null;
  flow_version: number | null;
  name: string;
  ticket_id: string;
  ticket_number: number;
  trigger: string;
  state: RunState;
  result: string | null;
  steps: FlowTestStep[];
  error: string | null;
  depth: number;
  node_id: string | null;
  until_reply: boolean;
  resume_at: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface RunFilters {
  rule_id?: string;
  flow_id?: string;
  ticket_id?: string;
  state?: string;
  trigger?: string;
}

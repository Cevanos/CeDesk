"""Workflows (feature-gap plan 5.2, 5.3), the rules that need no database.

A flow is a graph the visual builder draws. It is built on the rule engine: a condition
step checks the same conditions a rule does (`rules.parse_condition`, `rules.matches`), and
an action step does one rule action (`rules.parse_action`, `rules.act`). What flows add is
branching and waiting.

Stored as JSON (`hd_flow.graph`, checked by `parse_graph` before saving):

    {"nodes": [
        {"id": "start", "type": "trigger", "event": "created"},
        {"id": "vip", "type": "condition",
         "conditions": [{"kind": "contact_vip", "value": true}]},
        {"id": "hello", "type": "action", "action": {"kind": "send_email", "body": "..."}},
        {"id": "wait", "type": "wait", "minutes": 2880, "until": "customer_replied"},
        {"id": "again", "type": "action", "action": {"kind": "send_email", "body": "..."}}
     ],
     "edges": [
        {"from": "start", "to": "vip"},
        {"from": "vip", "to": "hello", "when": "yes"},
        {"from": "hello", "to": "wait"},
        {"from": "wait", "to": "again", "when": "timeout"}
     ]}

- Exactly one trigger node (no edge into it). Its event is the flow's trigger: created,
  customer_replied, sla_breached or snooze_ended.
- A condition node has 1-10 conditions (all must match) and leaves by `yes` or `no`.
- An action node does one action and goes on by `next`.
- A wait node waits `minutes` (1 minute to 30 days) and goes on by `next`; with
  `"until": "customer_replied"` it goes on as soon as the customer replies (`replied`), or
  after `minutes` without a reply (`timeout`).
- A node with no edge for the way it leaves ends the run there. Each node has at most one
  edge per way out, so a run is always one path.
- Every node must be reachable from the trigger, and the graph has no loop unless the
  loop goes through a wait (a run that comes back round waits each time, and a run waits
  at most `MAX_WAITS` times).
- Node ids are 1-40 of a-z, 0-9, _ and -. A node may carry `"position": {"x": 0, "y": 0}`
  and `"label"` for the builder; the engine ignores them.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from cedesk.helpdesk.domain import rules
from cedesk.helpdesk.domain.rules import Event, Facts, RuleError, State, Targets

MAX_FLOWS = 50  # per workspace
MAX_NODES = 50
MAX_EDGES = 100
MAX_WAIT_MINUTES = 43_200  # 30 days
MAX_WAITS = 20  # waits in one run
MAX_STEPS = 100  # steps in one run, all slices together
NODE_ID = re.compile(r"^[a-z0-9_-]{1,40}$")
LABEL_MAX = 80


class FlowError(ValueError):
    """The message is for the Admin."""


class NodeType(StrEnum):
    trigger = "trigger"
    condition = "condition"
    action = "action"
    wait = "wait"


class Way(StrEnum):
    """How a node is left (an edge's `when`)."""

    next = "next"
    yes = "yes"
    no = "no"
    replied = "replied"
    timeout = "timeout"


WAYS: Mapping[NodeType, frozenset[Way]] = {
    NodeType.trigger: frozenset({Way.next}),
    NodeType.condition: frozenset({Way.yes, Way.no}),
    NodeType.action: frozenset({Way.next}),
    NodeType.wait: frozenset({Way.next, Way.replied, Way.timeout}),
}


@dataclass(frozen=True)
class Node:
    id: str
    type: NodeType
    event: Event | None = None  # trigger
    conditions: tuple[rules.Condition, ...] = ()  # condition
    action: rules.Action | None = None  # action
    minutes: int = 0  # wait
    until_reply: bool = False  # wait: until the customer replies, or `minutes` pass
    extra: Mapping[str, Any] = field(default_factory=dict)  # position, label: kept as is


@dataclass(frozen=True)
class Graph:
    nodes: Mapping[str, Node]
    edges: Mapping[tuple[str, Way], str]  # (from, way) -> to
    start: str  # the trigger node's id

    @property
    def event(self) -> Event:
        trigger = self.nodes[self.start].event
        assert trigger is not None
        return trigger

    def next(self, node_id: str, way: Way) -> str | None:
        return self.edges.get((node_id, way))

    def conditions(self) -> list[rules.Condition]:
        return [c for n in self.nodes.values() for c in n.conditions]

    def actions(self) -> list[rules.Action]:
        return [n.action for n in self.nodes.values() if n.action is not None]


def _node_id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not NODE_ID.match(value):
        raise FlowError(f"{what} is 1-40 of a-z, 0-9, _ and -.")
    return value


def _extra(raw: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    position = raw.get("position")
    if position is not None:
        if (
            not isinstance(position, Mapping)
            or set(position) - {"x", "y"}
            or not all(
                isinstance(v, int | float) and not isinstance(v, bool) and abs(v) < 1e6
                for v in position.values()
            )
        ):
            raise FlowError('A node\'s position is {"x": number, "y": number}.')
        out["position"] = dict(position)
    label = raw.get("label")
    if label is not None:
        if not isinstance(label, str) or len(label) > LABEL_MAX:
            raise FlowError(f"A node's label is text of at most {LABEL_MAX} characters.")
        out["label"] = label.strip()
    return out


def parse_node(raw: Any) -> Node:
    if not isinstance(raw, Mapping):
        raise FlowError("Each node is an object with an `id` and a `type`.")
    node_id = _node_id(raw.get("id"), "A node id")
    try:
        kind = NodeType(str(raw.get("type")))
    except ValueError as exc:
        raise FlowError(f"Node {node_id}: type is trigger, condition, action or wait.") from exc
    extra = _extra(raw)
    try:
        if kind is NodeType.trigger:
            try:
                event = Event(str(raw.get("event")))
            except ValueError as exc:
                allowed = ", ".join(e.value for e in Event)
                raise FlowError(f"Node {node_id}: event is one of {allowed}.") from exc
            return Node(node_id, kind, event=event, extra=extra)
        if kind is NodeType.condition:
            conditions = raw.get("conditions")
            if not isinstance(conditions, list) or not 1 <= len(conditions) <= rules.MAX_PARTS:
                raise FlowError(f"Node {node_id}: 1-{rules.MAX_PARTS} conditions.")
            if not all(isinstance(c, Mapping) for c in conditions):
                raise FlowError(f"Node {node_id}: each condition is an object with a `kind`.")
            return Node(
                node_id, kind, conditions=tuple(rules.parse_condition(c) for c in conditions),
                extra=extra,
            )  # fmt: skip
        if kind is NodeType.action:
            action = raw.get("action")
            if not isinstance(action, Mapping):
                raise FlowError(f"Node {node_id}: `action` is an object with a `kind`.")
            return Node(node_id, kind, action=rules.parse_action(action), extra=extra)
        minutes = raw.get("minutes")
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or not 1 <= minutes <= MAX_WAIT_MINUTES
        ):
            raise FlowError(f"Node {node_id}: minutes is 1-{MAX_WAIT_MINUTES} (30 days).")
        until = raw.get("until")
        if until not in (None, "customer_replied"):
            raise FlowError(f"Node {node_id}: until is customer_replied or left out.")
        return Node(node_id, kind, minutes=minutes, until_reply=until is not None, extra=extra)
    except RuleError as exc:
        raise FlowError(f"Node {node_id}: {exc}") from exc


def parse_graph(raw: Any) -> Graph:
    """Checks a graph as the builder sent it (or as stored). Raises FlowError."""
    if not isinstance(raw, Mapping):
        raise FlowError("A graph is an object with `nodes` and `edges`.")
    unknown = set(raw) - {"nodes", "edges"}
    if unknown:
        raise FlowError(f"A graph has only nodes and edges (not {', '.join(sorted(unknown))}).")
    raw_nodes, raw_edges = raw.get("nodes"), raw.get("edges", [])
    if not isinstance(raw_nodes, list) or not 2 <= len(raw_nodes) <= MAX_NODES:
        raise FlowError(f"A flow has 2-{MAX_NODES} nodes.")
    if not isinstance(raw_edges, list) or len(raw_edges) > MAX_EDGES:
        raise FlowError(f"A flow has at most {MAX_EDGES} edges.")
    nodes: dict[str, Node] = {}
    for item in raw_nodes:
        node = parse_node(item)
        if node.id in nodes:
            raise FlowError(f"Two nodes have the id {node.id}.")
        nodes[node.id] = node
    triggers = [n.id for n in nodes.values() if n.type is NodeType.trigger]
    if len(triggers) != 1:
        raise FlowError("A flow has exactly one trigger node.")
    start = triggers[0]
    trigger_event = nodes[start].event
    assert trigger_event is not None

    edges: dict[tuple[str, Way], str] = {}
    for item in raw_edges:
        if not isinstance(item, Mapping) or set(item) - {"from", "to", "when"}:
            raise FlowError('Each edge is {"from": id, "to": id, "when": way}.')
        src = _node_id(item.get("from"), "An edge's from")
        dst = _node_id(item.get("to"), "An edge's to")
        if src not in nodes or dst not in nodes:
            raise FlowError(f"The edge {src} → {dst} names a node that isn't there.")
        if dst == start:
            raise FlowError("Nothing leads back into the trigger.")
        if src == dst:
            raise FlowError(f"Node {src} can't lead to itself.")
        source = nodes[src]
        default = Way.timeout if source.until_reply else Way.next
        try:
            way = Way(str(item["when"])) if item.get("when") is not None else default
        except ValueError as exc:
            raise FlowError(f"The edge from {src}: unknown when.") from exc
        allowed = _ways(source)
        if way not in allowed:
            names = " or ".join(sorted(w.value for w in allowed))
            raise FlowError(f"The edge from {src} ({source.type.value}) is {names}.")
        if (src, way) in edges:
            raise FlowError(f"Node {src} has two {way.value} edges.")
        edges[(src, way)] = dst

    graph = Graph(nodes, edges, start)
    _check_reachable(graph)
    _check_loops(graph)
    try:
        rules.check_conditions(trigger_event, graph.conditions())
    except RuleError as exc:
        raise FlowError(str(exc)) from exc
    return graph


def _ways(node: Node) -> frozenset[Way]:
    if node.type is NodeType.wait:
        return frozenset({Way.replied, Way.timeout}) if node.until_reply else frozenset({Way.next})
    return WAYS[node.type]


def _check_reachable(graph: Graph) -> None:
    seen = {graph.start}
    todo = [graph.start]
    while todo:
        here = todo.pop()
        for (src, _), dst in graph.edges.items():
            if src == here and dst not in seen:
                seen.add(dst)
                todo.append(dst)
    lost = sorted(set(graph.nodes) - seen)
    if lost:
        raise FlowError(f"Not reachable from the trigger: {', '.join(lost)}.")


def _check_loops(graph: Graph) -> None:
    """A loop must go through a wait: without the wait nodes' edges, no cycle."""
    after: dict[str, list[str]] = {n: [] for n in graph.nodes}
    for (src, _), dst in graph.edges.items():
        if graph.nodes[src].type is not NodeType.wait:
            after[src].append(dst)
    state: dict[str, int] = {}  # 1 visiting, 2 done

    def visit(node: str) -> None:
        state[node] = 1
        for nxt in after[node]:
            if state.get(nxt) == 1:
                raise FlowError(f"The steps loop at {nxt} without a wait: add a wait step.")
            if nxt not in state:
                visit(nxt)
        state[node] = 2

    for node in graph.nodes:
        if node not in state:
            visit(node)


def stored_node(node: Node) -> dict[str, Any]:
    out: dict[str, Any] = {"id": node.id, "type": node.type.value}
    if node.type is NodeType.trigger:
        assert node.event is not None
        out["event"] = node.event.value
    elif node.type is NodeType.condition:
        out["conditions"] = [rules.stored_condition(c) for c in node.conditions]
    elif node.type is NodeType.action:
        assert node.action is not None
        out["action"] = rules.stored_action(node.action)
    else:
        out["minutes"] = node.minutes
        if node.until_reply:
            out["until"] = "customer_replied"
    return out | dict(node.extra)


def stored_graph(graph: Graph) -> dict[str, Any]:
    """The JSON kept: checked, in one fixed shape (edges always have `when`)."""
    return {
        "nodes": [stored_node(n) for n in graph.nodes.values()],
        "edges": [
            {"from": src, "to": dst, "when": way.value} for (src, way), dst in graph.edges.items()
        ],
    }


# Running


@dataclass(frozen=True)
class Step:
    """One node a run passed: for the run log and test mode. `said` is the line an action
    wrote ("Tag vip"), or None when it changed nothing."""

    node: str
    type: NodeType
    outcome: str  # condition: yes/no; action: done/no change; wait: waiting/replied/...
    said: str | None = None
    reason: str | None = None  # a condition that said no: why (test mode only, not stored)

    def stored(self) -> dict[str, Any]:
        out: dict[str, Any] = {"node": self.node, "type": self.type.value, "outcome": self.outcome}
        if self.said:
            out["said"] = self.said
        return out


@dataclass(frozen=True)
class Walk:
    state: State
    steps: tuple[Step, ...]
    done: tuple[str, ...]  # what the actions said, in order
    wait_at: str | None = None  # the wait node the run now waits on (None: it ended)
    wait_minutes: int = 0
    until_reply: bool = False
    stopped: str | None = None  # why it stopped early (too many steps or waits)


def walk(
    graph: Graph,
    facts: Facts,
    state: State,
    targets: Targets,
    *,
    at: str | None = None,
    way: Way = Way.next,
    steps_before: int = 0,
    waits_before: int = 0,
    reasons: bool = False,
) -> Walk:
    """Runs the flow from the trigger (`at` None), or from the wait node `at`, leaving it
    by `way` (resumed: next, replied or timeout). Stops at the next wait, at a node with no
    edge onward, or at the step and wait limits. Pure: what it changes is in the returned
    state (its `effects` for the service to do)."""
    steps: list[Step] = []
    done: list[str] = []
    here = graph.next(graph.start if at is None else at, way)
    while here is not None:
        if steps_before + len(steps) >= MAX_STEPS:
            return Walk(state, tuple(steps), tuple(done), stopped="Too many steps in one run.")
        node = graph.nodes[here]
        if node.type is NodeType.condition:
            ok = all(rules.matches(c, facts, state) for c in node.conditions)
            reason = None
            if reasons and not ok:  # test mode only: a live run never reads it
                reason = "; ".join(
                    rules.why(c, facts, state)
                    for c in node.conditions
                    if not rules.matches(c, facts, state)
                )
            steps.append(Step(node.id, node.type, "yes" if ok else "no", reason=reason))
            here = graph.next(node.id, Way.yes if ok else Way.no)
        elif node.type is NodeType.action:
            assert node.action is not None
            state, said = rules.act(node.action, state, targets.types, targets.teams, targets)
            steps.append(Step(node.id, node.type, "done" if said else "no change", said))
            if said:
                done.append(said)
            here = graph.next(node.id, Way.next)
        elif node.type is NodeType.wait:
            if waits_before >= MAX_WAITS:
                return Walk(state, tuple(steps), tuple(done), stopped="Too many waits in one run.")
            steps.append(Step(node.id, node.type, "waiting"))
            return Walk(
                state, tuple(steps), tuple(done), wait_at=node.id, wait_minutes=node.minutes,
                until_reply=node.until_reply,
            )  # fmt: skip
        else:  # pragma: no cover - nothing leads into the trigger
            break
    return Walk(state, tuple(steps), tuple(done))


def line(name: str, done: Sequence[str]) -> str:
    """The ticket's system line for a flow step slice that did something."""
    return f"Workflow {name} → {' · '.join(done)}"


# The builder's palette (GET /automation/catalog)

_ID = {"type": "string", "format": "uuid"}
_TAG = "^[a-z0-9][a-z0-9_-]{0,39}$"
_MINUTES = {"type": "integer", "minimum": 1, "maximum": rules.MAX_MINUTES}


def _schema(props: Mapping[str, Any], required: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(props),
        "required": list(required),
        "additionalProperties": False,
    }


def _enum(values: Sequence[str]) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string", "enum": list(values)}, "minItems": 1}


_Entry = tuple[str, str, dict[str, Any], list[str], str | None]


def catalog(
    *,
    channels: Sequence[str],
    statuses: Sequence[str],
    sentiments: Sequence[str],
    sla_states: Sequence[str],
    priorities: Sequence[str],
) -> dict[str, Any]:
    """Everything the builder can draw, each with a JSON schema of its parameters (the
    `kind` or `type` is fixed by the entry). The lists of values come from the enums the
    engine checks against."""
    conditions: list[_Entry] = [
        ("text_contains_any", "Text contains any of",
         {"words": {"type": "array", "items": {"type": "string", "maxLength": rules.MAX_WORD},
                    "minItems": 1, "maxItems": rules.MAX_WORDS}}, ["words"], None),
        ("channel_in", "Channel is", {"channels": _enum(channels)}, ["channels"], None),
        ("to_address_contains", "Sent to an address containing",
         {"text": {"type": "string", "maxLength": 254}}, ["text"], None),
        ("status_in", "Status is", {"statuses": _enum(statuses)}, ["statuses"], None),
        ("sentiment_in", "Mood is", {"sentiments": _enum(sentiments)}, ["sentiments"], None),
        ("sla_in", "SLA is", {"states": _enum(sla_states)}, ["states"],
         "Not on the created trigger."),
        ("contact_field", "Contact field",
         {"key": {"type": "string", "description": "A contact field key (GET /contact-fields)."},
          "op": {"type": "string", "enum": [o.value for o in rules.FieldOp]},
          "value": {"type": ["string", "number", "boolean"],
                    "description": "Not with is_set or not_set. Checked against the field."}},
         ["key", "op"], None),
        ("contact_vip", "Contact is VIP", {"value": {"type": "boolean"}}, ["value"], None),
        ("contact_in_segment", "Contact is in a segment",
         {"segment_ids": {"type": "array", "items": _ID, "minItems": 1,
                          "maxItems": rules.MAX_SEGMENTS},
          "negate": {"type": "boolean", "default": False,
                     "description": "true: in none of them."}},
         ["segment_ids"], None),
        ("time_since", "Time since the last reply",
         {"of": {"type": "string", "enum": [s.value for s in rules.Since]},
          "op": {"type": "string", "enum": [c.value for c in rules.Compare]},
          "minutes": _MINUTES},
         ["of", "op", "minutes"], "No reply yet counts from when the ticket was made."),
        ("snoozed", "Ticket is snoozed", {"value": {"type": "boolean"}}, ["value"], None),
        ("has_followers", "Ticket has followers", {"value": {"type": "boolean"}}, ["value"],
         None),
    ]  # fmt: skip
    actions: list[_Entry] = [
        ("set_type", "Set the type",
         {"type_id": _ID, "only_if_empty": {"type": "boolean", "default": False}},
         ["type_id"], None),
        ("set_team", "Set the team", {"team_id": _ID}, ["team_id"], "Routes the ticket again."),
        ("set_priority", "Set the priority",
         {"priority": {"type": "string", "enum": list(priorities)},
          "keep_urgent": {"type": "boolean", "default": False}}, ["priority"], None),
        ("set_status", "Set the status",
         {"status": {"type": "string", "enum": list(statuses)}}, ["status"], None),
        ("add_tag", "Add a tag", {"tag": {"type": "string", "pattern": _TAG}}, ["tag"], None),
        ("snooze", "Snooze", {"minutes": _MINUTES}, ["minutes"],
         "Pending until then; never on a resolved ticket."),
        ("add_follower", "Add a follower", {"member_id": _ID}, ["member_id"],
         "Active members who can see the ticket."),
        ("send_email", "Email the customer",
         {"body": {"type": "string", "minLength": 1, "maxLength": rules.EMAIL_MAX,
                   "description": "Placeholders: {{customer.name}}, {{customer.first_name}}, "
                                  "{{agent.name}} (the workspace), {{fields.<key>}}."}},
         ["body"], "A reply from the workspace, sent the way replies are."),
        ("call_webhook", "Call a webhook", {"webhook_id": _ID}, ["webhook_id"],
         "Admins only. One of Settings > Webhooks; signed and retried like its others."),
    ]  # fmt: skip
    return {
        "triggers": [
            {"event": "created", "label": "Ticket created"},
            {"event": "customer_replied", "label": "Customer replied"},
            {"event": "sla_breached", "label": "SLA breached"},
            {"event": "snooze_ended", "label": "Snooze ended"},
        ],
        "conditions": [
            {"kind": k, "label": label, "params": _schema(p, req), **({"note": n} if n else {})}
            for k, label, p, req, n in conditions
        ],
        "actions": [
            {"kind": k, "label": label, "params": _schema(p, req), **({"note": n} if n else {})}
            for k, label, p, req, n in actions
        ],
        "nodes": [
            {
                "type": "trigger",
                "ways": ["next"],
                "params": _schema(
                    {"event": {"type": "string", "enum": [e.value for e in Event]}}, ["event"]
                ),
            },
            {
                "type": "condition",
                "ways": ["yes", "no"],
                "params": _schema(
                    {
                        "conditions": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": rules.MAX_PARTS,
                            "items": {"type": "object", "description": "A condition."},
                        }
                    },
                    ["conditions"],
                ),
            },
            {
                "type": "action",
                "ways": ["next"],
                "params": _schema(
                    {"action": {"type": "object", "description": "One action."}}, ["action"]
                ),
            },
            {
                "type": "wait",
                "ways": ["next", "replied", "timeout"],
                "params": _schema(
                    {
                        "minutes": {"type": "integer", "minimum": 1, "maximum": MAX_WAIT_MINUTES},
                        "until": {"type": "string", "enum": ["customer_replied"]},
                    },
                    ["minutes"],
                ),
                "note": "Without until: leaves by next. With until: replied or timeout.",
            },
        ],
        "limits": {
            "nodes": MAX_NODES,
            "edges": MAX_EDGES,
            "conditions_per_step": rules.MAX_PARTS,
            "wait_minutes": MAX_WAIT_MINUTES,
            "waits_per_run": MAX_WAITS,
            "steps_per_run": MAX_STEPS,
            "flows": MAX_FLOWS,
            "rules": rules.MAX_RULES,
        },
    }

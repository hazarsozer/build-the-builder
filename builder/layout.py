"""Presentation for the Freya dashboard's visual editor: node positions and call-variable samples.

Nothing here changes what the agent does. `place` only adds `position` to nodes; `sample_call_variables` only gives
the dashboard defaults for variables every real call passes itself (a value passed with the call wins over a
test_variable of the same name, docs/workflow-format.md "Call variables and test_variables").

Where the editor reads positions (from the dashboard bundle, 2026-10-11): a workflow record has a `nodePositions`
map {node_key: {"x", "y"}} next to `workflow`, and each node definition may carry `position: {"x", "y"}`; the editor
uses the map first and the node's own `position` as a fallback. The compiled workflow carries the node `position`
(it travels with the exported JSON); freya.py sends the same map as the record's `nodePositions`.
"""
import json
import re
from collections import deque

COL = 560   # x step between columns: a ~300 px card plus room for the edges between columns
ROW = 460   # y step between stacked siblings: cards are ~250-400 px tall
TARGET_KEYS = ("passthrough_default_transition", "max_turn_transition")


def targets(node):
    """Every node a node can move to, in its own order: transitions, then the passthrough and max-turn targets."""
    out = [t.get("target_node") for t in node.get("transitions") or []]
    out += [node.get(k) for k in TARGET_KEYS]
    return [t for t in dict.fromkeys(out) if t]


def _is_end(node):
    return node.get("node_type") == "end_call"


def _is_close(key, node, nodes):
    """A close node: named close_*, or a non-end node whose every target ends the call."""
    if _is_end(node):
        return False
    tg = [t for t in targets(node) if t in nodes]
    return key.startswith("close_") or (bool(tg) and all(_is_end(nodes[t]) for t in tg))


def positions(wf):
    """{node_key: {"x", "y"}} for every node and global node: columns by BFS depth from the start node, siblings
    stacked in discovery order, close nodes in the second-to-last column, end-call nodes in the last one, global
    nodes in a column of their own left of the start. Deterministic: the same workflow gives the same layout."""
    nodes = wf.get("nodes") or {}
    start = wf.get("initial_node")
    close = {k for k, n in nodes.items() if _is_close(k, n, nodes)}
    end = {k for k, n in nodes.items() if _is_end(n)}
    depth = {}
    if start in nodes:
        depth[start] = 0
        todo = deque([start])
        while todo:
            k = todo.popleft()
            for t in targets(nodes[k]):
                if t in nodes and t not in depth:
                    depth[t] = depth[k] + 1
                    todo.append(t)
    order = list(depth) + [k for k in nodes if k not in depth]   # BFS discovery order, then anything unreached
    main = [k for k in order if k not in close and k not in end]
    last = max([depth.get(k, 0) for k in main] or [0])
    col = {}
    for k in main:
        col[k] = depth[k] if k in depth else last + 1   # unreached nodes (prune normally drops them) get a column
    tail = max(col.values(), default=-1) + 1
    for k in order:
        if k in close:
            col[k] = tail
        elif k in end:
            col[k] = tail + (1 if close else 0)
    columns = {}
    for k in order:
        columns.setdefault(col[k], []).append(k)
    tallest = max((len(v) for v in columns.values()), default=1)

    def ys(c):   # centre each column against the tallest one
        top = (tallest - len(columns[c])) * ROW / 2
        return {k: top + i * ROW for i, k in enumerate(columns[c])}

    # fewer crossing edges: left to right, order each column by the mean height of its parents in earlier columns
    preds = {}
    for k in order:
        for t in targets(nodes[k]):
            if t in nodes:
                preds.setdefault(t, []).append(k)
    y = {}
    for c in sorted(columns):
        y.update(ys(c))
        keyed = []
        for i, k in enumerate(columns[c]):
            up = [y[p] for p in preds.get(k, []) if col[p] < c]
            keyed.append((sum(up) / len(up) if up else y[k], i, k))
        columns[c] = [k for _, _, k in sorted(keyed)]
        y.update(ys(c))
    pos = {}
    for c in columns:
        for k, v in ys(c).items():
            pos[k] = {"x": c * COL, "y": int(v)}
    for i, k in enumerate(wf.get("global_nodes") or {}):
        pos[k] = {"x": -COL, "y": i * ROW}
    return pos


def place(wf):
    """Write each node's position into the workflow (in place) and return the position map."""
    pos = positions(wf)
    for group in ("nodes", "global_nodes"):
        for k, n in (wf.get(group) or {}).items():
            n["position"] = dict(pos[k])
    return pos


def node_positions(wf):
    """The record-level `nodePositions` map for a workflow that carries node positions (empty when none do)."""
    out = {}
    for group in ("nodes", "global_nodes"):
        for k, n in (wf.get(group) or {}).items():
            p = n.get("position") if isinstance(n, dict) else None
            if isinstance(p, dict) and "x" in p and "y" in p:
                out[k] = {"x": p["x"], "y": p["y"]}
    return out


# ------------------------------------------------------------------ call-variable samples

def call_variable_names(spec):
    """The variables a call passes: the spec's call_variables plus any the address template reads."""
    names = list(spec.get("call_variables") or [])
    names += re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", spec.get("address_as") or "")
    return list(dict.fromkeys(names))


def _words(name):
    """A variable name's words: split on underscores, other non-word characters and camelCase ("customerId")."""
    return [w.lower() for w in re.split(r"[\W_]+|(?<=[a-z0-9])(?=[A-Z])", name) if w]


def _sample(name, spec):
    """customer_id -> c_test; the clock (now) -> no entry, so setup reads the real time; any other call variable -> "".
    final-review-2 I5: a spoken sample ("Ms", a made-up name or company) would be read out on a call that omits the
    variable, i.e. the wrong person's details; "" says nothing, as before A19."""
    low = name.lower()
    ws = _words(name)
    if low == "now" or set(ws) & {"now", "datetime", "timestamp"}:
        return None
    if (ws and ws[-1] == "id") or low == "id":
        return low[:1] + "_test"   # customer_id -> c_test
    return ""


def sample_call_variables(spec, wf):
    """test_variables for the dashboard: customer_id gets c_test and every other call variable but the clock an empty
    default, so a call that omits one never speaks a placeholder or an invented value. The dashboard still warns that
    the clock will not resolve, and its "Call Agent" button has no name to say (I5 trade-off).

    Only call variables, never a model-filled tool parameter. Tool parameters such as `field`, `answer`, `cb_date`,
    `cb_time` have a `{{name}}` default that the model fills in on each tool call; a test_variable of the same name
    would be a set variable, and a set variable overrides the model's input (seen in earlier runs). The dashboard
    keeps warning about those placeholders; that warning is expected and harmless.

    A call that passes a variable overrides its default here."""
    written = _written_names(wf)
    out = {}
    for name in call_variable_names(spec):
        if name in written:
            continue
        v = _sample(name, spec)
        if v is not None:
            out[name] = v
    return out


def _written_names(wf):
    """Names the workflow itself writes (extraction variables, response mappings): never a call input."""
    out = set()
    for group in ("nodes", "global_nodes"):
        for n in (wf.get(group) or {}).values():
            out |= {e.get("name") for e in n.get("extraction_schema") or []}
            out |= {m.get("variable") for m in n.get("response_mapping") or []}
            for t in (n.get("tools") or []) + (n.get("pre_actions") or []):
                out |= {m.get("variable") for m in t.get("response_mapping") or []}
    out.discard(None)
    return out


def tool_placeholders(wf):
    """Every exact `{{name}}` default of a tool or pre-action parameter: the model fills these on each call (or,
    for a call variable such as customer_id, the call does)."""
    return set(re.findall(r'"default": "\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}"', json.dumps(wf)))

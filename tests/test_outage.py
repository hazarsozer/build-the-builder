"""Outage resilience (AFTER-PLAN §8): a code node that fails must never strand the call.

Offline. For every code node of the reference specs, the node is failed in two ways:
  - "skip": it writes none of the keys it computes (the sandbox did not run it);
  - "raise@k": its own code runs and an exception escapes at the k-th variable write (writes before it stay).
Then the engine is simulated: passthrough nodes route on their conditions (code nodes downstream run for real,
the failed node keeps failing if it is entered again) until a conversation node or the end node is reached.

Checked there:
  - the end node: close_call gets an outcome of the openapi enum and an (outcome, reason) pair of the spec's
    outcome table, and the goodbye line is a real sentence;
  - a conversation node: every computed variable its spoken line or prompt names is set and not empty;
  - from that point on, every conversation node the call can still reach names only variables that are set on
    the way (code nodes on the way are assumed to work), and the end node is reached with outcome and farewell set.
The graph tests: every node reaches the end node, every conversation node has a turn cap with a target, and no
cycle consists of passthrough nodes only.
"""
import json
import re

import pytest

from builder.compile import compile as C

OA = json.load(open("tools/openapi.json"))
SPECS = {k: json.load(open("specs/reference/spec-%s.json" % k)) for k in ("a", "b")}
ENUM = OA["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["outcome"]["enum"]
CALL = {"customer_id": "c_test", "first_name": "Alex", "last_name": "Reyes", "title": "Mr", "now": "2026-10-14T11:00"}
PRODUCTS = [{"product_id": "p1", "type": "credit_card", "card_last4": "4821", "past_due": 420.0, "days_late": 6,
             "minimum_payment": 35.0},
            {"product_id": "p2", "type": "personal_loan", "past_due": 300.0, "days_late": 20, "instalment_amount": 150.0}]
PH = re.compile(r"\{\{([^{}]+)\}\}")
WRITE = re.compile(r"vars\[(['\"])([A-Za-z_][A-Za-z0-9_]*)\1\]\s*=(?!=)|vars\.update\(dict\(([^)]*)\)")


class Kill(BaseException):
    """An exception the snippet's own `except Exception` does not catch (a sandbox kill, a timeout)."""


class FailAt(dict):
    """vars that raise Kill at the k-th write statement (a dict.update is one statement)."""

    def __init__(self, data, k):
        super().__init__(data)
        self.k = k

    def _tick(self):
        if self.k == 0:
            raise Kill()
        self.k -= 1

    def __setitem__(self, key, value):
        self._tick()
        super().__setitem__(key, value)

    def update(self, *a, **kw):
        self._tick()
        super().update(*a, **kw)


# ---------------------------------------------------------------- workflow helpers

def code_of(node):
    return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def writes_of(code):
    out = set()
    for m in WRITE.finditer(code):
        if m.group(2):
            out.add(m.group(2))
        else:
            out |= set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", m.group(3)))
    return out


def run_ok(node, v):
    exec(code_of(node), {"vars": v})


def holds(c, v):
    op = c.get("operator")
    if op == "always":
        return True
    if op == "matches_intent":
        return False
    if "logic" in c:
        r = [holds(x, v) for x in c["conditions"]]
        return any(r) if c["logic"] == "or" else all(r)
    var = c["variable"]
    if var not in v or v[var] is None:
        return False
    if op == "is_set":
        return True
    if op == "equals":
        return str(v[var]) == str(c["operand"])
    if op == "greater_than":
        try:
            return float(v[var]) > float(c["operand"])
        except (TypeError, ValueError):
            return False
    raise AssertionError("operator %r" % op)


def route(node, v):
    for t in node.get("transitions") or []:
        if not t.get("is_tool_call") and holds(t["condition"], v):
            return t["target_node"]
    return node.get("passthrough_default_transition")


def targets(node):
    out = [t["target_node"] for t in node.get("transitions") or []]
    out += [node[k] for k in ("passthrough_default_transition", "max_turn_transition") if node.get(k)]
    return out


def spoken(node):
    """Text with placeholders the engine fills: spoken lines, the prompt, the end message."""
    return json.dumps([node.get("on_enter"), node.get("task_message"), node.get("terminal")])


def computed(w):
    """Variables some code (node or code tool) or a response mapping of the workflow writes."""
    out = set()
    for n in w["nodes"].values():
        if n["node_type"] == "code":
            out |= writes_of(code_of(n))
        out |= node_sets(n)
    return out


def node_sets(n):
    """Variables a non-code node can set on its way out (tool code, mappings, extraction)."""
    out = {m["variable"] for m in n.get("response_mapping") or []}
    out |= {x["name"] for x in n.get("extraction_schema") or []}
    for t in (n.get("tools") or []) + (n.get("pre_actions") or []):
        out |= {m["variable"] for m in t.get("response_mapping") or []}
        code = ((t.get("function") or {}).get("parameters") or {}).get("properties", {}).get("code", {}).get("default")
        for c in (code, t.get("pre_process_code"), t.get("post_process_code")):
            if c:
                out |= writes_of(c)
    return out


def outcome_pairs(spec):
    return {(o["outcome"], o.get("reason") or None) for o in spec["outcomes"]}


def reasonless(spec):
    return {o["outcome"] for o in spec["outcomes"] if not o.get("reason")}


def reach(w, start, without=()):
    seen, todo = {start}, [start]
    while todo:
        for t in targets(w["nodes"][todo.pop()]):
            if t not in seen and t not in without:
                seen.add(t)
                todo.append(t)
    return seen


# ---------------------------------------------------------------- states before a node

def upstream(w):
    """(U1: after setup, U2: after a loaded account) — realistic variables before a node runs."""
    n = w["nodes"]
    u1 = dict(CALL)
    run_ok(n["setup"], u1)
    u2 = dict(u1, acct_products_json=json.dumps(PRODUCTS), acct_first_json=json.dumps(PRODUCTS[0]))
    run_ok(n["prepare_account"], u2)
    assert u2["account_state"] == "ok"
    return u1, u2


def entry_states(w, x):
    n = w["nodes"]
    if x == "setup":
        return [dict(CALL)]
    u1, u2 = upstream(w)
    if x == "prepare_account":
        return [{k: v for k, v in u2.items() if k in u1 or k.startswith("acct_")}]
    out = []
    if x in reach(w, "setup", without={"prepare_account"}):
        out.append(u1)
    if x in reach(w, "prepare_account", without={"readback"}):
        out.append(u2)
    after_only = (reach(w, "after_record") - reach(w, "readback", without={"after_record"})) if "after_record" in n else set()
    if "readback" in n and x in reach(w, "readback") - reach(w, "prepare_account", without={"readback"}) - after_only:
        # several products: the read-back is entered only after the set tool wrote it
        out.append(dict(u2, mp_phase="readback", mp_readback="Product 1: 420.00 on 16 October. Do you confirm?"))
    if "after_record" in n and x in reach(w, "after_record") - reach(w, "prepare_account", without={"after_record"}):
        # A18: after_record is entered only after wrap_recorded wrote the recorded sentence (mp_recorded == yes)
        out.append(dict(u2, mp_phase="confirmed", mp_recorded="yes", mp_said="Your promise for your credit card is recorded.",
                        mp_close="close_promise_recorded"))
    return out


def failures(node, v):
    """(label, vars after the failure) for every failure mode of a code node entered with vars v."""
    yield "skip", dict(v)
    for k in range(500):
        fv = FailAt(v, k)
        try:
            exec(code_of(node), {"vars": fv})
        except Kill:
            yield "raise@%d" % k, dict(fv)
            continue
        break  # fewer writes than k: the code ran to the end


def retry_twins(w):
    """Nodes entered only because their twin failed (a retry); failing both is a double failure, not tested."""
    return {k for k in w["nodes"] if k.endswith("_retry") and w["nodes"][k]["node_type"] == "code"}


# ---------------------------------------------------------------- the walk after a failure

def walk(w, x, v):
    """Route from the failed node through passthrough nodes. Returns the stop node; v is updated in place."""
    n = w["nodes"]
    cur = route(n[x], v)
    path = [x]
    while True:
        assert cur in n, (path, cur)
        path.append(cur)
        assert len(path) < 40, ("passthrough loop", path)
        node = n[cur]
        if node["node_type"] in ("conversation", "end_call"):
            return cur, path
        if node["node_type"] == "code" and cur != x:
            run_ok(node, v)
        cur = route(node, v)


def close_request(w, v):
    """The close_call request the engine sends at the end of the call. A field whose default is one unresolved
    placeholder is filled by one model call (probe 3a362703: it guessed an outcome); here the guess is the enum's
    first value and "guessed" for a reason. Then the pre-hook runs. Returns (url, body)."""
    t = [t for t in w["tools"] if t["function"]["name"] == "close_call"][0]
    props = t["function"]["parameters"]["properties"]
    body = {}
    for f, p in props["body"]["properties"].items():
        d = p.get("default")
        m = PH.fullmatch(d) if isinstance(d, str) else None
        if m and m.group(1) not in v:
            body[f] = (p.get("enum") or ["guessed"])[0]
            continue
        body[f] = str(v[m.group(1)]) if m else d
    cfg = {"url": props["url"]["default"], "body": body}
    exec(t["pre_process_code"], {"config": cfg, "vars": dict(v), "result": None})
    return cfg["url"], cfg["body"]


def close_body(w, v):
    """The body close_call records at the bank: none when the pre-hook kept the request on the read-only route."""
    url, body = close_request(w, v)
    return body if url.endswith("/close_call") else {}


def is_close(node):
    return node["node_type"] == "code" and "outcome" in writes_of(code_of(node))


def check_stop(w, spec, stop, v, why, no_close_ok=False):
    """no_close_ok: the failed node is a close node, so the call may end with no outcome (as on a hang-up),
    never with a wrong one."""
    node = w["nodes"][stop]
    comp = computed(w)
    if node["node_type"] == "end_call" and no_close_ok and "outcome" not in v:
        assert "outcome" not in close_body(w, v) and "reason" not in close_body(w, v), why
        assert isinstance(v.get("farewell"), str) and v["farewell"].strip().endswith("."), (why, v.get("farewell"))
    elif node["node_type"] == "end_call":
        assert v.get("outcome") in ENUM, (why, v.get("outcome"))
        pair = (v["outcome"], v.get("reason") or None)
        assert pair in outcome_pairs(spec) or v["outcome"] in reasonless(spec), (why, pair)
        assert isinstance(v.get("farewell"), str) and v["farewell"].strip().endswith("."), (why, v.get("farewell"))
        assert "{" not in v["farewell"], (why, v["farewell"])
    for name in set(PH.findall(spoken(node))) & comp:
        val = v.get(name)
        assert val is not None and str(val).strip() not in ("", "None"), (why, stop, name, val)


def check_onward(w, x, stop, v, why, no_close_ok=False):
    """Every conversation node reachable from `stop` names only variables set on the way."""
    n = w["nodes"]
    comp = computed(w)
    start = frozenset(k for k, val in v.items() if val is not None)
    seen = set()
    todo = [(stop, start)]
    while todo:
        k, s = todo.pop()
        if (k, s) in seen:
            continue
        seen.add((k, s))
        node = n[k]
        missing = (set(PH.findall(spoken(node))) & comp) - s
        assert not missing, (why, "reaches", k, "without", sorted(missing))
        if node["node_type"] == "end_call":
            need = {"farewell"} if no_close_ok else {"outcome", "farewell"}
            assert need <= s, (why, "ends without", need - s)
            continue
        if node["node_type"] == "code" and k != x:
            s = s | writes_of(code_of(node))
        else:
            s = s | node_sets(node)
        for t in targets(node):
            todo.append((t, frozenset(s)))


CASES = []
for _key, _spec in SPECS.items():
    _w = C(_spec, OA)
    for _x, _node in _w["nodes"].items():
        if _node["node_type"] == "code" and _x not in retry_twins(_w):
            CASES.append((_key, _x))


@pytest.mark.parametrize("spec_key,node", CASES, ids=["%s-%s" % c for c in CASES])
def test_failed_code_node_reaches_a_valid_close(spec_key, node):
    spec = SPECS[spec_key]
    w = C(spec, OA)
    states = entry_states(w, node)
    assert states, node
    for i, before in enumerate(states):
        for label, v in failures(w["nodes"][node], before):
            why = "%s/%s state %d %s" % (spec_key, node, i, label)
            stop, path = walk(w, node, v)
            nc = is_close(w["nodes"][node])
            check_stop(w, spec, stop, v, why + " via " + ">".join(path), no_close_ok=nc)
            check_onward(w, node, stop, v, why, no_close_ok=nc)


@pytest.mark.parametrize("spec_key", sorted(SPECS))
def test_graph_has_no_dead_end_or_uncapped_loop(spec_key):
    w = C(SPECS[spec_key], OA)
    n = w["nodes"]
    end = [k for k, x in n.items() if x["node_type"] == "end_call"]
    assert end
    close = [t for t in w["tools"] if t["function"]["name"] == "close_call"]
    assert close and close[0]["config"].get("alwaysRunsAt") == ["end_of_call"]
    for k, node in n.items():
        assert set(end) & reach(w, k), ("dead end", k)
        if node["node_type"] == "conversation":
            assert node.get("max_turn_transition") in n, ("no turn cap target", k)
        elif node["node_type"] != "end_call":
            assert node.get("passthrough") and node["transitions"][-1]["condition"] == {"operator": "always"}, k
    # passthrough-only cycles would spin without a user turn
    passthrough = {k for k, x in n.items() if x["node_type"] not in ("conversation", "end_call")}
    state = {}

    def dfs(k):
        state[k] = 1
        for t in targets(n[k]):
            if t in passthrough:
                assert state.get(t) != 1, ("passthrough cycle through", k, t)
                if t not in state:
                    dfs(t)
        state[k] = 2

    for k in passthrough:
        if k not in state:
            dfs(k)


@pytest.mark.parametrize("spec_key", sorted(SPECS))
def test_close_call_prehook_drops_an_empty_reason(spec_key):
    """A21 item 7: the values come from the close node's vars (never the body the engine or the model filled)."""
    w = C(SPECS[spec_key], OA)
    pre = [t for t in w["tools"] if t["function"]["name"] == "close_call"][0]["pre_process_code"]
    for vs, want in [({"outcome": "no_promise", "reason": ""}, {"outcome": "no_promise"}),
                     ({"outcome": "no_promise", "reason": "refused"}, {"outcome": "no_promise", "reason": "refused"}),
                     ({"outcome": "promise", "reason": "service_error"}, {"outcome": "promise"})]:
        cfg = {"url": "x", "body": {"customer_id": "c1", "outcome": "not_verified", "reason": "failed"}}
        exec(pre, {"config": cfg, "vars": dict(vs, close_ready="yes"), "result": None})
        assert cfg["body"] == dict(want, customer_id="c1"), vs
        assert cfg["url"].endswith("/close_call")


@pytest.mark.parametrize("spec_key", sorted(SPECS))
def test_setup_writes_a_farewell_but_no_outcome(spec_key):
    w = C(SPECS[spec_key], OA)
    for node in ("setup", "setup_retry"):
        v = dict(CALL)
        run_ok(w["nodes"][node], v)
        assert "outcome" not in v and "reason" not in v
        assert v["farewell"].endswith("Goodbye.")


@pytest.mark.parametrize("spec_key", sorted(SPECS))
def test_early_hangup_sends_no_outcome(spec_key):
    """A call that ends before any close node ran (a hang-up) sends close_call without outcome or reason: no false
    close next to a promise or callback that was recorded."""
    w = C(SPECS[spec_key], OA)
    u1, u2 = upstream(w)
    for v in (dict(CALL), u1, u2):
        body = close_body(w, v)
        assert "outcome" not in body and "reason" not in body, body
    # a close that ran is sent as written
    v = dict(u2)
    run_ok(w["nodes"]["close_refused"], v)
    assert close_body(w, v)["outcome"] == "no_promise"
    v = dict(u2)
    run_ok(w["nodes"]["close_promise_recorded"], v)
    assert close_body(w, v)["outcome"] == "promise" and "reason" not in close_body(w, v)


def test_harness_catches_a_close_without_defaults():
    """Self-check: a workflow whose closes are the only writers of outcome fails the walk when a close fails."""
    w = C(SPECS["a"], OA)
    v = dict(CALL)
    stop, _ = walk(w, "close_refused", v)
    assert stop == "goodbye"
    with pytest.raises(AssertionError):
        check_stop(w, SPECS["a"], stop, v, "no setup ran")

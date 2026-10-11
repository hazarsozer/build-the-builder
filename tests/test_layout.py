"""A19: the compiled workflow is laid out for the dashboard editor and declares samples for its call variables."""
import copy
import json

import pytest

import freya
from builder import layout
from builder.compile import compile as C
from builder.lint import lint

OA = json.load(open("tools/openapi.json"))
SPECS = {k: json.load(open("specs/reference/spec-%s.json" % k)) for k in ("a", "b")}
CARD_W, CARD_H = 300, 400   # the editor's node card, generous


def strip(wf):
    w = copy.deepcopy(wf)
    w.pop("test_variables", None)
    for g in ("nodes", "global_nodes"):
        for n in w.get(g, {}).values():
            n.pop("position", None)
    return w


@pytest.fixture(params=sorted(SPECS))
def case(request):
    s = SPECS[request.param]
    return s, C(s, OA, base_url="https://bank.example/v1")


def test_every_node_has_a_position(case):
    _, w = case
    for k, n in w["nodes"].items():
        p = n["position"]
        assert set(p) == {"x", "y"} and all(isinstance(v, int) for v in p.values()), k


def test_no_two_cards_overlap(case):
    _, w = case
    boxes = [(k, n["position"]["x"], n["position"]["y"]) for k, n in w["nodes"].items()]
    for i, (a, ax, ay) in enumerate(boxes):
        for b, bx, by in boxes[i + 1:]:
            assert abs(ax - bx) >= CARD_W or abs(ay - by) >= CARD_H, (a, b)


def test_layout_is_deterministic(case):
    s, w = case
    assert json.dumps(C(copy.deepcopy(s), OA, base_url="https://bank.example/v1")) == json.dumps(w)


def test_columns_flow_from_start_to_close_to_end(case):
    _, w = case
    n = w["nodes"]
    x = {k: v["position"]["x"] for k, v in n.items()}
    assert x[w["initial_node"]] == min(x.values())
    ends = [k for k, v in n.items() if v["node_type"] == "end_call"]
    closes = [k for k in n if k.startswith("close_")]
    others = [k for k in n if k not in ends and k not in closes]
    assert closes and min(x[k] for k in closes) > max(x[k] for k in others)
    assert min(x[k] for k in ends) > max(x[k] for k in closes)
    assert x["opening"] < x["identity"] < x["negotiate"]


def test_positions_add_nothing_else(case):
    """Only `position` and test_variables are new: lint still clean, and nothing in the graph refers to positions."""
    s, w = case
    assert [p for p in lint(w, s) if not p.startswith("warning:")] == []
    assert "\"position\":" not in json.dumps(strip(w))
    assert set(w) == {"initial_node", "system_prompt", "nodes", "global_nodes", "tools", "test_variables"}


def test_global_nodes_go_left_of_the_start():
    wf = {"initial_node": "a", "nodes": {"a": {"node_type": "conversation", "transitions": [{"target_node": "b"}]},
                                         "b": {"node_type": "end_call"}},
          "global_nodes": {"g1": {"node_type": "conversation"}, "g2": {"node_type": "conversation"}}}
    pos = layout.place(wf)
    assert pos["g1"]["x"] < pos["a"]["x"] and pos["g1"]["y"] != pos["g2"]["y"]
    assert wf["global_nodes"]["g2"]["position"] == pos["g2"]
    assert layout.node_positions(wf) == pos


def test_unreached_node_still_gets_a_place():
    wf = {"initial_node": "a", "nodes": {"a": {"node_type": "conversation"}, "z": {"node_type": "conversation"}}}
    pos = layout.positions(wf)
    assert set(pos) == {"a", "z"} and pos["a"] != pos["z"]


# ------------------------------------------------------------------ test_variables

def test_test_variables_are_the_call_variables(case):
    s, w = case
    tv = w["test_variables"]
    assert set(tv) == set(s["call_variables"])
    assert tv["customer_id"] == "c_test" and tv["title"] in ("Mr", "Ms") and tv["first_name"] and tv["last_name"]
    assert tv["now"] == s["examples"][0]["today"] + "T10:00"
    assert all(isinstance(v, str) and v for v in tv.values())


def test_never_a_model_filled_placeholder(case):
    """A set variable overrides the model's tool input: field, answer, cb_*, sp_*, ... must stay undeclared."""
    s, w = case
    filled = layout.tool_placeholders(w) - set(s["call_variables"])
    assert {"field", "answer", "cb_date", "cb_time"} <= filled
    assert not set(w["test_variables"]) & filled


def test_every_case_passes_every_test_variable(case):
    """A value passed with the call wins over a test variable: every case the builder generates (and every
    public case) passes each declared variable itself, so no sample reaches a simulation."""
    from builder import cases
    s, w = case
    gen = [c for seed in (0, 1) for c in cases.generate(s, OA, w["test_variables"]["now"], n=10, seed=seed)]
    assert gen
    for c in gen:
        passed = set(c["variables"]) | {"customer_id"}   # the practice bank session adds customer_id
        assert set(w["test_variables"]) <= passed, c.get("id")
    for c in json.load(open("public-test-set.json")):
        assert set(w["test_variables"]) <= set(c["variables"]) | {"customer_id"}, c["id"]


def test_extra_call_variable_gets_a_sample():
    s = copy.deepcopy(SPECS["a"])
    s["call_variables"].append("company_name")
    w = C(s, OA)
    assert w["test_variables"]["company_name"] == "Example Ltd"


def test_written_variable_is_never_declared():
    s = copy.deepcopy(SPECS["a"])
    w = C(s, OA)
    ext = {e["name"] for n in w["nodes"].values() for e in n.get("extraction_schema") or []}
    assert ext and not ext & set(w["test_variables"])


# ------------------------------------------------------------------ freya client

def test_client_sends_record_level_node_positions(case, monkeypatch):
    _, w = case
    sent = []
    monkeypatch.setattr(freya, "api", lambda m, p, body=None, **q: sent.append((m, p, body)) or {"workflow": {"id": "w", "agentId": "a"}})
    freya.create_workflow("x", w)
    body = sent[0][2]
    assert body["workflow"] is w
    assert body["nodePositions"] == {k: n["position"] for k, n in w["nodes"].items()}


def test_client_sends_no_positions_for_an_unlaid_workflow(monkeypatch):
    sent = []
    monkeypatch.setattr(freya, "api", lambda m, p, body=None, **q: sent.append(body) or {"workflow": {"id": "w", "agentId": "a"}})
    freya.create_workflow("x", {"initial_node": "a", "nodes": {"a": {}}})
    assert "nodePositions" not in sent[0]

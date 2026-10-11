"""Task A15: bench generalisation gaps (insurance, ids). Fixtures are the bench run's extracted specs."""
import copy
import json
import re

from builder.compile import compile as C
from builder.lint import lint

INS = json.load(open("tests/fixtures/bench-insurance.spec.json"))
INS_OA = json.load(open("bench/insurance/openapi.json"))
IDS = json.load(open("tests/fixtures/bench-ids.spec.json"))
IDS_OA = json.load(open("bench/ids/openapi.json"))
OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))


def _code(node):
    return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def _close_of(node):
    """(outcome, reason) a close node sends."""
    c = _code(node)
    return re.search(r"OUTCOME = '([^']*)'", c).group(1), re.search(r"REASON = '([^']*)'", c).group(1) or None


def _ins_product(pid, typ, days, due=186.4, floor=62.15):
    return {"product_id": pid, "type": typ, "policy_number_last4": "3307", "premium_due": due,
            "days_overdue": days, "minimum_instalment": floor}


def _run_wrap(spec, oa, node, prods, statuses, recorded=(), now="2026-10-15T10:00"):
    w = C(spec, oa, base_url="https://bank.example/v1")
    v = {"now": now, "acct_products_json": json.dumps(prods)}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    st = json.loads(v["mp_state"])
    for pid, stt in statuses.items():
        st["p"][pid]["status"] = stt
        if stt == "agreed":
            st["p"][pid].update(amount=prods[0]["premium_due"], date="2026-10-16")
    v["mp_state"] = json.dumps(st)
    exec(_code(w["nodes"]["record_plan"]), {"vars": v})
    for i, pid in enumerate(recorded, 1):
        assert v["rec%d_pid" % i] == pid
        v["rec%d_status" % i] = "recorded"
    exec(_code(w["nodes"][node]), {"vars": v})
    return w, v


# ---------------------------------------------------------------- 1. the promise close takes the brief's outcome name

def test_ins_recorded_promise_closes_with_the_briefs_promise_outcome():
    """IN-01/02/07/08: one promise recorded, the wrap closed as no_promise/refused (literal 'promise' key)."""
    prods = [_ins_product("p1", "car_insurance", 9)]
    w, v = _run_wrap(INS, INS_OA, "wrap_recorded", prods, {"p1": "agreed"}, recorded=["p1"])
    t = v["mp_close"]
    assert _close_of(w["nodes"][t]) == ("promise_to_pay", None), t
    assert "I understand" not in v["mp_said"]
    fin = [x["target_node"] for x in w["nodes"]["finish"]["transitions"]]
    assert t in fin


def test_ins_two_recorded_promises_close_as_promise_to_pay():
    prods = [_ins_product("p1", "car_insurance", 9), _ins_product("p2", "home_insurance", 30, 615, 205)]
    w, v = _run_wrap(INS, INS_OA, "wrap_recorded", prods, {"p1": "agreed", "p2": "agreed"}, recorded=["p1", "p2"])
    assert _close_of(w["nodes"][v["mp_close"]]) == ("promise_to_pay", None)


def test_ins_no_promise_still_closes_refused():
    prods = [_ins_product("p1", "car_insurance", 9)]
    w, v = _run_wrap(INS, INS_OA, "wrap_done", prods, {"p1": "refused"})
    assert _close_of(w["nodes"][v["mp_close"]]) == ("no_promise", "refused")


def test_renamed_no_promise_outcome_is_used():
    """A brief that renames no_promise too: the wrap closes with the refused event's outcome name."""
    s = copy.deepcopy(INS)
    for e in s["events"]:
        if e.get("outcome") == "no_promise":
            e["outcome"] = "unpaid"
        if e.get("else_outcome") == "no_promise":
            e["else_outcome"] = "unpaid"
    for o in s["outcomes"]:
        if o["outcome"] == "no_promise":
            o["outcome"] = "unpaid"
    oa = copy.deepcopy(INS_OA)
    txt = json.dumps(oa).replace('"no_promise"', '"unpaid"')
    oa = json.loads(txt)
    prods = [_ins_product("p1", "car_insurance", 9)]
    w, v = _run_wrap(s, oa, "wrap_done", prods, {"p1": "refused"})
    assert _close_of(w["nodes"][v["mp_close"]]) == ("unpaid", "refused")
    w, v = _run_wrap(s, oa, "wrap_recorded", prods, {"p1": "agreed"}, recorded=["p1"])
    assert _close_of(w["nodes"][v["mp_close"]]) == ("promise_to_pay", None)


def test_ins_compiles_lint_clean():
    w = C(INS, INS_OA, base_url="https://bank.example/v1")
    assert [p for p in lint(w, INS) if not p.startswith("warning:")] == []

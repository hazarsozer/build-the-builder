"""Brief B: several products. Library rules, compiled graph, and the code/hook state machine run on case data."""
import copy
import datetime as dt
import json

from builder.compile import compile as C
from builder.lib import dates as D
from builder.lint import lint

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
CASES = {c["id"]: c for c in json.load(open("public-test-set.json"))}
CAL = B["calendar"]
T = dt.date(2026, 10, 14)


# ---------------------------------------------------------------- library

def test_close_reason_first_non_paid_note():
    assert D.close_reason([("p1", "paid"), ("p2", "refused")], 0, None, "first_non_paid_note") == ("no_promise", "refused")


def test_close_reason_all_paid():
    assert D.close_reason([("p1", "paid"), ("p2", "paid")], 0, None, "first_non_paid_note") == ("paid", None)


def test_close_reason_promise_wins():
    assert D.close_reason([("p2", "refused")], 1, None, "first_non_paid_note") == ("promise", None)


def test_close_reason_transfer_wins_over_promise():
    assert D.close_reason([("p2", "refused")], 1, "restructuring", "first_non_paid_note") == ("transferred", "restructuring")


def test_close_reason_last_note_and_fallback():
    assert D.close_reason([("p1", "refused"), ("p2", "dispute")], 0, None, "last_note") == ("no_promise", "dispute")
    assert D.close_reason([], 0, None, "first_non_paid_note", fallback=("no_promise", "refused")) == ("no_promise", "refused")


def _products(case):
    return [dict(p, product_id="p%d" % (i + 1)) for i, p in enumerate(CASES[case]["products"])]


def test_plan_products_per_product_deadlines():
    plans = D.plan_products(_products("PUB-13"), T, B["bands"], CAL, B["products"])
    assert [p["product_id"] for p in plans] == ["p1", "p2"]
    assert plans[0]["band"] == "early" and plans[0]["deadline"] == dt.date(2026, 11, 2)
    assert plans[1]["band"] == "late" and plans[1]["deadline"] == dt.date(2026, 10, 23)
    assert plans[0]["floor"] == 90.0 and plans[0]["full"] == 300.0
    assert plans[1]["attempts"] == [("current", dt.date(2026, 10, 16)), ("floor", dt.date(2026, 10, 23))]


def test_shared_date_capped_to_product_deadline():
    plans = D.plan_products(_products("PUB-13"), T, B["bands"], CAL, B["products"])
    got = D.apply_shared_date(plans, dt.date(2026, 10, 30))
    assert got == [("p1", dt.date(2026, 10, 30), False), ("p2", dt.date(2026, 10, 23), True)]


# ---------------------------------------------------------------- compiled graph

def wf(spec=B):
    return C(spec, OA, base_url="https://bank.example/v1")


def _code(node):
    return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def _tool(node, name):
    return [t for t in node.get("tools", []) + node.get("pre_actions", []) if t["function"]["name"] == name][0]


def test_spec_b_lint_clean():
    assert lint(wf(), B) == []


def test_spec_b_add_note_nodes_use_openapi_enum():
    w = wf()
    oa_enum = OA["paths"]["/add_note"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["reason"]["enum"]
    for i in (1, 2, 3):
        t = _tool(w["nodes"]["note_%d" % i], "add_note")
        assert t["function"]["parameters"]["properties"]["body"]["properties"]["reason"]["enum"] == oa_enum
        assert t["function"]["parameters"]["properties"]["url"]["default"] == "https://bank.example/v1/add_note"


def test_spec_b_negotiate_has_set_product_not_create_promise():
    n = wf()["nodes"]["negotiate"]
    names = [t["function"]["name"] for t in n["tools"]]
    assert names == ["set_product"]
    body = n["tools"][0]["function"]["parameters"]["properties"]["body"]["properties"]
    assert set(body["outcome"]["enum"]) == {"agreed", "refused", "dispute", "paid", "confirmed"}
    for i in (1, 2, 3):
        assert _tool(wf()["nodes"]["record_%d" % i], "create_promise")


def test_spec_a_node_set_unchanged():
    w = C(A, OA)
    assert set(w["nodes"]) == {
        "setup", "opening", "third_party", "identity", "load_account", "prepare_account", "negotiate",
        "retry_promise", "goodbye", "transfer_restructuring", "transfer_general", "transfer_support",
        "close_promise_recorded", "close_paid", "close_dispute", "close_restructuring", "close_human",
        "close_hardship", "close_hardship_transfer", "close_refused", "close_not_talk", "close_service_error",
        "close_failed", "close_declined", "close_callback", "close_no_callback", "close_wrong_number",
        "close_deceased", "close_unclear", "close_account_error"}
    assert [t["function"]["name"] for t in w["nodes"]["negotiate"]["tools"]] == ["create_promise"]


def test_transfers_skip_notes():
    w = wf()
    for k, n in w["nodes"].items():
        if k.startswith("transfer_"):
            for t in n["transitions"]:
                assert t["target_node"].startswith("close_"), (k, t)
                assert "note" not in t["target_node"]


def test_negotiate_routes_on_phase():
    n = wf()["nodes"]["negotiate"]
    by_target = {t["target_node"]: t for t in n["transitions"]}
    assert "record_plan" in by_target and "confirmed" in json.dumps(by_target["record_plan"]["condition"])
    assert "wrap_done" in by_target and "done" in json.dumps(by_target["wrap_done"]["condition"])
    assert "wrap_hardship" in by_target  # declined support
    assert "wrap_not_talk" in by_target and by_target["wrap_not_talk"].get("is_tool_call")


# ---------------------------------------------------------------- state machine on case data

class Sim:
    """Runs setup + prepare_account, then set_product hook calls, and the engine-run chain."""

    def __init__(self, case, spec=B):
        self.w = wf(spec)
        self.case = CASES[case]
        self.v = dict(self.case["variables"], customer_id="c1",
                      acct_products_json=json.dumps(_products(case)))
        self.run("setup")
        self.run("prepare_account")
        assert self.v["account_state"] == "ok", self.v.get("account_error")
        self.t = _tool(self.w["nodes"]["negotiate"], "set_product")

    def run(self, node):
        exec(_code(self.w["nodes"][node]), {"vars": self.v})

    def say(self, **body):
        cfg = {"url": "x", "body": dict(body)}
        exec(self.t["pre_process_code"], {"vars": self.v, "config": cfg})
        r = {"status": "success", "data": {"ok": True, "products": []}}
        exec(self.t["post_process_code"], {"vars": self.v, "result": r})
        assert "products" not in r["data"]
        return r["data"]["what_to_do_next"]

    def hook(self, node, tool, result):
        t = _tool(self.w["nodes"][node], tool)
        cfg = {"url": "x", "body": {}}
        exec(t.get("pre_process_code") or "", {"vars": self.v, "config": cfg})
        if t.get("post_process_code"):
            exec(t["post_process_code"], {"vars": self.v, "result": result})
        return cfg["body"]

    def chain(self, fail=()):
        """Engine part after `confirmed` (or a wrap): records, notes, close. Returns (promises, notes, close node)."""
        node, promises, notes, guard = None, [], [], 0
        node = "record_plan" if self.v.get("mp_phase") == "confirmed" else self.end
        calls = {"create_promise": 0}
        while not node.startswith("close_") and guard < 40:
            guard += 1
            n = self.w["nodes"][node]
            if n["node_type"] == "code":
                self.run(node)
            else:
                tool = n["pre_actions"][0]["function"]["name"]
                if tool == "create_promise":
                    calls["create_promise"] += 1
                    bad = calls["create_promise"] in fail
                    r = {"status": "error" if bad else "success", "data": {"ok": not bad}}
                    body = self.hook(node, tool, r)
                    if not bad:
                        promises.append((body["product_id"], round(float(body["amount"]), 2), body["date"]))
                    for m in n.get("response_mapping", []):
                        self.v[m["variable"]] = str(not bad).lower()
                    self.v[node + "_response.data.ok"] = str(not bad).lower()
                elif tool == "add_note":
                    body = self.hook(node, tool, {"status": "success", "data": {"ok": True}})
                    notes.append((body["product_id"], body["reason"]))
            node = self.route(n)
        exec(_code(self.w["nodes"][node]), {"vars": self.v})
        return sorted(promises), sorted(notes), node, self.v["outcome"], self.v.get("reason")

    def route(self, n):
        for t in n["transitions"]:
            if t.get("is_tool_call"):
                continue
            if self.cond(t["condition"]):
                return t["target_node"]
        raise AssertionError("no route from " + n["name"])

    def cond(self, c):
        if c.get("operator") == "always":
            return True
        if "logic" in c:
            res = [self.cond(x) for x in c["conditions"]]
            return any(res) if c["logic"] == "or" else all(res)
        val = self.v.get(c["variable"])
        if c["operator"] == "equals":
            return str(val) == str(c["operand"])
        if c["operator"] == "greater_than":
            try:
                return float(val) > float(c["operand"])
            except (TypeError, ValueError):
                return False
        raise AssertionError(c)


def test_prepare_states_every_product_then_asks_all():
    s = Sim("PUB-11")
    first = s.w["nodes"]["negotiate"]["on_enter"]["text"]
    assert "{{debt_statement_text}}" in first and "{{first_ask_text}}" in first
    stmt = s.v["debt_statement_text"]
    assert stmt.count("days late") == 3 and "credit card ending 2288" in stmt and "car loan" in stmt
    assert s.v["first_ask_text"] == B["negotiation"]["first_ask_all"]
    assert "p1" in s.v["products_text"] and "p3" in s.v["products_text"]


def test_single_product_uses_single_statement():
    s = Sim("PUB-11")
    v = dict(CASES["PUB-11"]["variables"], acct_products_json=json.dumps(_products("PUB-11")[:1]))
    exec(_code(s.w["nodes"]["setup"]), {"vars": v})
    exec(_code(s.w["nodes"]["prepare_account"]), {"vars": v})
    assert v["first_ask_text"] == B["negotiation"]["first_ask"]
    assert v["debt_statement_text"].startswith("I am calling from the collections team")


def test_pub11_all_three_one_date():
    s = Sim("PUB-11")
    msg = s.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-16")
    assert s.v["mp_phase"] == "readback"
    assert "Do you confirm?" in msg and msg.count("Friday 16 October 2026") >= 1
    assert "record" not in msg.lower()
    s.say(product_id="ALL", outcome="confirmed")
    assert s.v["mp_phase"] == "confirmed"
    promises, notes, close, outcome, reason = s.chain()
    assert promises == [("p1", 220.0, "2026-10-16"), ("p2", 640.0, "2026-10-16"), ("p3", 75.0, "2026-10-16")]
    assert notes == [] and close == "close_promise_recorded" and outcome == "promise"


def test_pub12_one_promise_two_refusals_with_attempt_gate():
    s = Sim("PUB-12")
    s.say(product_id="p1", outcome="agreed", amount="510", date="2026-10-15")
    assert s.v["mp_phase"] == "open"
    msg = s.say(product_id="p2", outcome="refused", attempts_made=1)
    assert "attempt 2" in msg.lower() and s.v["mp_phase"] == "open"
    s.say(product_id="p2", outcome="refused", attempts_made=2)
    msg = s.say(product_id="p3", outcome="refused", attempts_made=1)
    assert s.v["mp_phase"] == "readback" and "credit card ending 9015" in msg
    s.say(product_id="ALL", outcome="confirmed")
    promises, notes, close, outcome, reason = s.chain()
    assert promises == [("p1", 510.0, "2026-10-15")]
    assert notes == [("p2", "refused"), ("p3", "refused")]
    assert (outcome, reason) == ("promise", None)


def test_pub13_shared_date_capped_and_said():
    s = Sim("PUB-13")
    msg = s.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-30")
    assert "Friday 30 October 2026" in msg and "Friday 23 October 2026" in msg
    s.say(product_id="ALL", outcome="confirmed")
    promises, notes, close, outcome, reason = s.chain()
    assert promises == [("p1", 300.0, "2026-10-30"), ("p2", 900.0, "2026-10-23")]


def test_single_product_date_after_deadline_is_refused_with_deadline():
    s = Sim("PUB-13")
    msg = s.say(product_id="p2", outcome="agreed", amount="900", date="2026-10-30")
    assert "Friday 23 October 2026" in msg and s.v["mp_phase"] == "open"
    assert json.loads(s.v["mp_state"])["p"]["p2"]["status"] == "pending"


def test_below_floor_and_weekend_rejected():
    s = Sim("PUB-13")
    msg = s.say(product_id="p1", outcome="agreed", amount="10", date="2026-10-16")
    assert "90.00" in msg and json.loads(s.v["mp_state"])["p"]["p1"]["status"] == "pending"
    msg = s.say(product_id="p1", outcome="agreed", amount="300", date="2026-10-17")
    assert "Monday 19 October 2026" in msg


def test_all_paid_closes_paid_with_notes():
    s = Sim("PUB-13")
    s.say(product_id="p1", outcome="paid")
    s.say(product_id="p2", outcome="paid")
    assert s.v["mp_phase"] == "done"
    s.end = "wrap_done"
    promises, notes, close, outcome, reason = s.chain()
    assert notes == [("p1", "paid"), ("p2", "paid")] and (outcome, reason) == ("paid", None)


def test_paid_then_refused_closes_refused():
    s = Sim("PUB-13")
    s.say(product_id="p1", outcome="paid")
    s.say(product_id="p2", outcome="refused", attempts_made=2)
    s.end = "wrap_done"
    promises, notes, close, outcome, reason = s.chain()
    assert notes == [("p1", "paid"), ("p2", "refused")] and (outcome, reason) == ("no_promise", "refused")


def test_dispute_does_not_end_and_is_noted():
    s = Sim("PUB-13")
    s.say(product_id="p1", outcome="dispute")
    assert s.v["mp_phase"] == "open"
    s.say(product_id="p2", outcome="agreed", amount="900", date="2026-10-20")
    s.say(product_id="ALL", outcome="confirmed")
    promises, notes, close, outcome, reason = s.chain()
    assert promises == [("p2", 900.0, "2026-10-20")] and notes == [("p1", "dispute")] and outcome == "promise"


def test_create_promise_fails_twice_on_one_product():
    s = Sim("PUB-13")
    s.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-20")
    s.say(product_id="ALL", outcome="confirmed")
    promises, notes, close, outcome, reason = s.chain(fail=(1, 2))
    assert promises == [("p2", 900.0, "2026-10-20")] and notes == [("p1", "service_error")]
    assert outcome == "promise"


def test_create_promise_fails_once_is_retried():
    s = Sim("PUB-13")
    s.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-20")
    s.say(product_id="ALL", outcome="confirmed")
    promises, notes, close, outcome, reason = s.chain(fail=(1,))
    assert len(promises) == 2 and notes == []


def test_only_failures_close_service_error():
    s = Sim("PUB-13")
    s.say(product_id="p1", outcome="agreed", amount="300", date="2026-10-20")
    s.say(product_id="p2", outcome="refused", attempts_made=2)
    s.say(product_id="ALL", outcome="confirmed")
    promises, notes, close, outcome, reason = s.chain(fail=(1, 2))
    assert promises == [] and notes == [("p1", "service_error"), ("p2", "refused")]
    assert (outcome, reason) == ("no_promise", "service_error")


def test_hardship_declined_notes_open_products():
    s = Sim("PUB-12")
    s.say(product_id="p1", outcome="refused", attempts_made=2)
    s.end = "wrap_hardship"
    promises, notes, close, outcome, reason = s.chain()
    assert notes == [("p1", "refused"), ("p2", "hardship"), ("p3", "hardship")]
    assert (outcome, reason) == ("no_promise", "refused")


def test_confirm_before_everything_settled_is_refused():
    s = Sim("PUB-12")
    s.say(product_id="p1", outcome="agreed", amount="510", date="2026-10-15")
    msg = s.say(product_id="ALL", outcome="confirmed")
    assert s.v["mp_phase"] == "open" and "personal loan" in msg


def test_change_at_readback_rereads():
    s = Sim("PUB-13")
    s.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-20")
    msg = s.say(product_id="p1", outcome="agreed", amount="300", date="2026-10-22")
    assert s.v["mp_phase"] == "readback" and "Thursday 22 October 2026" in msg


def test_product_reference_by_number_or_words():
    s = Sim("PUB-12")
    s.say(product_id="1", outcome="agreed", amount="510", date="2026-10-15")
    s.say(product_id="personal loan", outcome="refused", attempts_made=2)
    st = json.loads(s.v["mp_state"])["p"]
    assert st["p1"]["status"] == "agreed" and st["p2"]["status"] == "refused"


def test_hooks_never_raise():
    s = Sim("PUB-11")
    for body in [{}, {"product_id": None}, {"outcome": "agreed"}, {"product_id": "zz", "outcome": "agreed", "amount": "x", "date": "y"}]:
        cfg = {"body": body}
        exec(s.t["pre_process_code"], {"vars": s.v, "config": cfg})
        for r in [None, {}, {"data": None}, "x"]:
            exec(s.t["post_process_code"], {"vars": s.v, "result": r})
    exec(s.t["pre_process_code"], {"vars": {}, "config": {}})

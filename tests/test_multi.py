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
    body = n["tools"][0]["function"]["parameters"]["properties"]
    assert n["tools"][0]["function"]["handler"] == "__function__:code_exec"
    assert set(body["outcome"]["enum"]) == {"agreed", "refused", "dispute", "paid", "review"}
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
        "close_deceased", "close_unclear", "close_account_error",
        "human_ask_opening", "human_hold_opening", "human_ask_identity", "human_hold_identity",
        "human_ask_negotiate", "human_hold_negotiate",
        "human_back_opening", "human_back_identity", "human_back_negotiate"}
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
    assert "readback" in by_target and "readback" in json.dumps(by_target["readback"]["condition"])
    assert "record_plan" not in by_target
    rb = wf()["nodes"]["readback"]
    assert rb["on_enter"]["text"] == "{{mp_readback}}"
    rt = {t["target_node"]: t for t in rb["transitions"]}
    assert '"yes"' in json.dumps(rt["record_plan"]["condition"]) and "readback_reset" in rt
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
        g = {"vars": self.v, "params": dict(body)}
        exec(self.t["function"]["parameters"]["properties"]["code"]["default"], g)
        assert g["_result"]["what_to_do_next"] == self.v["mp_msg"]
        return self.v["mp_msg"]

    def confirm(self):
        """The readback node: the engine speaks mp_readback, the customer says yes."""
        assert self.v["mp_phase"] == "readback"
        self.v["rb_answer"] = "yes"
        self.end = self.route(self.w["nodes"]["readback"])
        assert self.end == "record_plan"
        return self.v["mp_readback"]

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
        node = self.end
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
                    self.notes_order = list(notes)
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
    s.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-16")
    assert s.v["mp_phase"] == "readback"
    msg = s.v["mp_readback"]
    assert "Do you confirm?" in msg and msg.count("Friday 16 October 2026") == 3
    assert "record" not in msg.lower()
    s.confirm()
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
    s.say(product_id="p3", outcome="refused", attempts_made=1)
    assert s.v["mp_phase"] == "readback" and "credit card ending 9015" in s.v["mp_readback"]
    s.confirm()
    promises, notes, close, outcome, reason = s.chain()
    assert promises == [("p1", 510.0, "2026-10-15")]
    assert notes == [("p2", "refused"), ("p3", "refused")]
    assert (outcome, reason) == ("promise", None)


def test_pub13_shared_date_capped_and_said():
    s = Sim("PUB-13")
    s.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-30")
    msg = s.v["mp_readback"]
    assert "Friday 30 October 2026" in msg and "Friday 23 October 2026" in msg
    s.confirm()
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
    s.confirm()
    promises, notes, close, outcome, reason = s.chain()
    assert promises == [("p2", 900.0, "2026-10-20")] and notes == [("p1", "dispute")] and outcome == "promise"


def test_create_promise_fails_twice_on_one_product():
    s = Sim("PUB-13")
    s.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-20")
    s.confirm()
    promises, notes, close, outcome, reason = s.chain(fail=(1, 2))
    assert promises == [("p2", 900.0, "2026-10-20")] and notes == [("p1", "service_error")]
    assert outcome == "promise"


def test_create_promise_fails_once_is_retried():
    s = Sim("PUB-13")
    s.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-20")
    s.confirm()
    promises, notes, close, outcome, reason = s.chain(fail=(1,))
    assert len(promises) == 2 and notes == []


def test_only_failures_close_service_error():
    s = Sim("PUB-13")
    s.say(product_id="p1", outcome="agreed", amount="300", date="2026-10-20")
    s.say(product_id="p2", outcome="refused", attempts_made=2)
    s.confirm()
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
    msg = s.say(product_id="ALL", outcome="review")
    assert s.v["mp_phase"] == "open" and "personal loan" in msg


def test_change_at_readback_rereads():
    s = Sim("PUB-13")
    s.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-20")
    s.v["rb_answer"] = "change"
    assert s.route(s.w["nodes"]["readback"]) == "readback_reset"
    s.run("readback_reset")
    assert s.v["mp_phase"] == "open"
    s.say(product_id="p1", outcome="agreed", amount="300", date="2026-10-22")
    assert s.v["mp_phase"] == "readback" and "Thursday 22 October 2026" in s.v["mp_readback"]
    assert s.v["rb_answer"] == "pending"


def test_product_reference_by_number_or_words():
    s = Sim("PUB-12")
    s.say(product_id="1", outcome="agreed", amount="510", date="2026-10-15")
    s.say(product_id="personal loan", outcome="refused", attempts_made=2)
    st = json.loads(s.v["mp_state"])["p"]
    assert st["p1"]["status"] == "agreed" and st["p2"]["status"] == "refused"


def test_hooks_never_raise():
    s = Sim("PUB-11")
    code = s.t["function"]["parameters"]["properties"]["code"]["default"]
    for body in [{}, {"product_id": None}, {"outcome": "agreed"}, {"product_id": "zz", "outcome": "agreed", "amount": "x", "date": "y"}]:
        exec(code, {"vars": s.v, "params": body})
    exec(code, {"vars": {}})


def test_counted_close_tool_name_matches_its_wrap():
    n = wf()["nodes"]["negotiate"]
    assert "transition_to_wrap_not_talk" in n["task_message"] and "transition_to_close_not_talk" not in n["task_message"]
    assert n["task_message"].count("   - refused") == 1


def test_set_product_code_needs_no_datetime_and_no_braces():
    t = _tool(wf()["nodes"]["negotiate"], "set_product")
    code = t["function"]["parameters"]["properties"]["code"]["default"]
    assert "datetime" not in code and "{{" not in code and "}}" not in code
    props = t["function"]["parameters"]["properties"]
    for k in ("product_id", "outcome", "amount", "date", "attempts_made"):
        assert "default" not in props[k], k


def test_api_mode_still_works(monkeypatch):
    from builder import multi
    monkeypatch.setattr(multi, "SET_MODE", "api")
    w = wf()
    t = _tool(w["nodes"]["negotiate"], "set_product")
    assert t["function"]["parameters"]["properties"]["url"]["default"].endswith("/get_account")
    v = {}
    s = Sim("PUB-13")
    cfg = {"url": "x", "body": {"product_id": "ALL", "outcome": "agreed", "amount": "full", "date": "2026-10-30"}}
    exec(t["pre_process_code"], {"vars": s.v, "config": cfg})
    r = {"status": "success", "data": {"ok": True, "products": []}}
    exec(t["post_process_code"], {"vars": s.v, "result": r})
    assert r["data"]["phase"] == "readback" and "products" not in r["data"] and v == {}


# ---------------------------------------------------------------- fix round 1

def test_r1_non_calendar_date_rejected():
    s = Sim("PUB-13")
    msg = s.say(product_id="p1", outcome="agreed", amount="300", date="2026-10-32")
    assert "NOT accepted" in msg and json.loads(s.v["mp_state"])["p"]["p1"]["status"] == "pending"


def test_r2_nan_amount_rejected():
    s = Sim("PUB-13")
    msg = s.say(product_id="p1", outcome="agreed", amount="nan", date="2026-10-20")
    assert "NOT accepted" in msg and json.loads(s.v["mp_state"])["p"]["p1"]["status"] == "pending"


def test_r3_missing_attempts_counts_as_zero():
    s = Sim("PUB-13")
    msg = s.say(product_id="p1", outcome="refused")
    assert "Not yet" in msg and json.loads(s.v["mp_state"])["p"]["p1"]["status"] == "pending"


def test_r4_model_cannot_confirm():
    t = _tool(wf()["nodes"]["negotiate"], "set_product")
    assert "confirmed" not in t["function"]["parameters"]["properties"]["outcome"]["enum"]
    s = Sim("PUB-13")
    s.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-20")
    s.say(product_id="ALL", outcome="confirmed")
    assert s.v["mp_phase"] == "readback"  # unknown outcome: nothing changes, recording only via the readback node


def test_max_turns_go_to_wrap():
    n = wf()["nodes"]["negotiate"]
    assert n["max_node_turns"] == 30 and n["max_turn_transition"] == "wrap_done"


def test_weekend_after_deadline_offers_deadline():
    s = Sim("PUB-13")  # p2 deadline Friday 23 October
    msg = s.say(product_id="p2", outcome="agreed", amount="900", date="2026-10-24")
    assert "Friday 23 October 2026" in msg and "Tuesday 27 October" not in msg
    s = Sim("PUB-12")  # weekend inside the deadline still offers the next business day
    msg = s.say(product_id="p3", outcome="agreed", amount="140", date="2026-10-17")
    assert "Monday 19 October 2026" in msg


def test_notes_in_get_account_order():
    spec = copy.deepcopy(B)
    spec["multi_product"]["order"] = "highest_past_due"
    s = Sim("PUB-12", spec)
    st = json.loads(s.v["mp_state"])
    assert st["order"][0] == "p2" and st["ga"] == ["p1", "p2", "p3"]
    s.say(product_id="p2", outcome="refused", attempts_made=2)
    s.say(product_id="p1", outcome="dispute")
    s.say(product_id="p3", outcome="paid")
    s.end = "wrap_done"
    promises, notes, close, outcome, reason = s.chain()
    assert [n for n in s.notes_order] == [("p1", "dispute"), ("p2", "refused"), ("p3", "paid")]
    assert (outcome, reason) == ("no_promise", "dispute")


def test_multi_prompt_size():
    # Our own prompt budget for the reference spec, not a platform limit (controller ruling, Task 7b M6).
    assert len(wf()["nodes"]["negotiate"]["task_message"]) <= 4600


def test_spec_b_hardship_conflict_matches_code():
    kept = [c for c in B["conflicts"] if c["field"] == "events[hardship]"][0]["kept"]
    assert "first product" in kept and "not paid" in kept


def test_validate_spec_b_against_freya_dry_run():  # live; skipped without FREYA_API_KEY
    import os
    import pytest
    if not os.environ.get("FREYA_API_KEY"):
        pytest.skip()
    import freya
    freya.validate_workflow("dryrun-b", wf())


# ---------------------------------------------------------------- fix round 2

def test_l1_mentioned_amount_rule_in_both_templates():
    for w in (wf(), C(A, OA)):
        t = w["nodes"]["negotiate"]["task_message"]
        assert "the <amount> I mentioned" in t and "in the same sentence as the date" in t
        assert 'never "The rest stays overdue." again' in t


def test_l3_readback_hears_transfers_and_support():
    w = wf()
    rb = w["nodes"]["readback"]
    enum = rb["extraction_schema"][0]["enum_values"]
    for v in ("restructuring", "human_first_request", "human_insists", "hardship"):
        assert v in enum, v
    by = {}
    for t in rb["transitions"]:
        by.setdefault(t["target_node"], []).append(json.dumps(t["condition"]))
    assert any('"restructuring"' in c for c in by["transfer_restructuring"])
    assert any('"human_first_request"' in c for c in by["human_ask_readback"])
    assert any('"hardship"' in c for c in by["readback_reset"])
    hold = w["nodes"]["human_hold_readback"]
    assert [t["target_node"] for t in hold["transitions"]] == ["transfer_general", "human_back_readback"]
    v = {"rb_answer": "human_first_request"}
    exec(_code(w["nodes"]["human_back_readback"]), {"vars": v})
    assert v["rb_answer"] == "pending"


def test_l4_mp_msg_sentinel():
    s = Sim("PUB-13")
    assert s.v["mp_msg"] == "none yet"
    s.v["mp_msg"] = ""
    s.run("human_back_negotiate")
    assert s.v["mp_msg"] == "none yet" and s.v["neg_event"] == "continue"
    s.run("readback_reset")
    assert "did not confirm" in s.v["mp_msg"]

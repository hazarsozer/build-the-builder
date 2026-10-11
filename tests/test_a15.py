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


# ---------------------------------------------------------------- 2. the brief's wrong-answer limit, counted in the hook

def _verify(w):
    n = w["nodes"]["identity"]
    return [t for t in n["tools"] if t["function"]["name"] == "verify_identity"][0]


def _answer(t, v, field, answer, data):
    """One verify_identity call: the pre-hook on the model's body, then the post-hook on the bank's result."""
    exec(t["pre_process_code"], {"vars": v, "config": {"url": "x", "body": {"field": field, "answer": answer}}})
    r = {"status": "success", "data": dict(data)}
    exec(t["post_process_code"], {"vars": v, "result": r})
    return r["data"]


WRONG = {"ok": True, "verified": False, "all_verified": False}


def test_ids_two_wrong_postcodes_end_the_check_though_the_bank_allows_more():
    """IDS-03: per_question, max 2. Our bank counts 3 in total and said wrong_answers_left 1 after two wrong postcodes."""
    w = C(IDS, IDS_OA, base_url="https://bank.example/v1")
    t, v = _verify(w), {"now": "2026-10-29T10:15"}
    d = _answer(t, v, "postcode", "BS3 4NF", dict(WRONG, wrong_answers_left=2))
    assert d["wrong_answers_left"] == 1 and "SAME question" in d["what_to_do_next"]
    d = _answer(t, v, "postcode", "BS4 3NE", dict(WRONG, wrong_answers_left=1))
    assert d["wrong_answers_left"] == 0 and "check has ended" in d["what_to_do_next"]
    fail = [x for x in w["nodes"]["identity"]["transitions"] if x["target_node"] == "close_failed"][0]
    assert "id_left" in json.dumps(fail)


def test_ids_per_question_one_wrong_on_each_is_fine():
    """IDS-04 / IDS-05: one wrong postcode, then the year: the year keeps its own two answers."""
    w = C(IDS, IDS_OA, base_url="https://bank.example/v1")
    t, v = _verify(w), {"now": "2026-11-06T16:00"}
    d = _answer(t, v, "postcode", "BS3 4NF", dict(WRONG, wrong_answers_left=2))
    assert d["wrong_answers_left"] == 1
    d = _answer(t, v, "postcode", "BS3 4NE", {"ok": True, "verified": True, "all_verified": False, "wrong_answers_left": 2})
    assert "correct" in d["what_to_do_next"]
    d = _answer(t, v, "birth_year", "1983", dict(WRONG, wrong_answers_left=1))
    assert d["wrong_answers_left"] == 1 and "SAME question" in d["what_to_do_next"], d
    d = _answer(t, v, "birth_year", "1982", dict(WRONG, wrong_answers_left=0))
    assert d["wrong_answers_left"] == 0


def test_hints_and_unreadable_answers_are_not_counted():
    w = C(IDS, IDS_OA, base_url="https://bank.example/v1")
    t, v = _verify(w), {"now": "2026-10-29T10:15"}
    for _ in range(3):
        d = _answer(t, v, "postcode", "BS3 4NF", dict(WRONG, wrong_answers_left=2, hint="partial_postcode"))
        assert d["wrong_answers_left"] == 2
    for _ in range(3):
        d = _answer(t, v, "postcode", "BS3 4NF", {"ok": False, "verified": False, "wrong_answers_left": 2})
        assert d["wrong_answers_left"] == 2
    d = _answer(t, v, "postcode", "BS3 4NF", dict(WRONG, wrong_answers_left=2))
    assert d["wrong_answers_left"] == 1


def test_total_scope_counts_across_questions():
    """Brief A shape (3 in total): a bank that never lowers wrong_answers_left still ends at the third wrong answer."""
    w = C(INS, INS_OA, base_url="https://bank.example/v1")
    t, v = _verify(w), {"now": "2026-10-26T10:00"}
    d = _answer(t, v, "date_of_birth", "1 March 1980", dict(WRONG, wrong_answers_left=9))
    assert d["wrong_answers_left"] == 2
    d = _answer(t, v, "postcode", "LS6 2TQ", dict(WRONG, wrong_answers_left=9))
    assert d["wrong_answers_left"] == 1
    d = _answer(t, v, "postcode", "LS6 3QT", dict(WRONG, wrong_answers_left=9))
    assert d["wrong_answers_left"] == 0 and "check has ended" in d["what_to_do_next"]


# ---------------------------------------------------------------- 3. a ladder date that is the n-th business day

def _ins_nth():
    s = copy.deepcopy(INS)
    s["bands"][1]["attempts"][1] = {"amount": "floor", "date": "nth_business_day", "n": 5}
    s["examples"].append({"kind": "attempt", "today": "2026-10-21", "days_late": 33, "attempt_index": 2,
                          "expect": "2026-10-28", "source": "IN-03: attempt 2 on the 5th business day"})
    return s


def test_attempt_date_nth_business_day():
    import datetime as dt
    from builder.lib import dates
    cal = INS["calendar"]
    t = dt.date(2026, 10, 21)
    dl = dates.deadline(t, {"rule": "nth_business_day", "n": 10}, cal)
    assert dl == dt.date(2026, 11, 4)
    assert dates.attempt_date(t, {"amount": "floor", "date": "nth_business_day", "n": 5}, dl, cal) == dt.date(2026, 10, 28)
    # capped at the deadline
    assert dates.attempt_date(t, {"amount": "floor", "date": "nth_business_day", "n": 30}, dl, cal) == dl
    # a plain kind still works
    assert dates.attempt_date(t, "deadline", dl, cal) == dl


def test_nth_business_day_spec_validates_and_needs_n():
    from builder.validate import validate
    s = _ins_nth()
    assert validate(s, INS_OA) == []
    bad = copy.deepcopy(s)
    del bad["bands"][1]["attempts"][1]["n"]
    assert validate(bad, INS_OA) != []


def test_in03_attempt_2_offers_the_5th_business_day():
    """IN-03: Wed 21 Oct, lapse-risk (33 days): attempt 2 is 137 on Wed 28 Oct, not the deadline 4 Nov."""
    s = _ins_nth()
    w = C(s, INS_OA, base_url="https://bank.example/v1")
    prods = [_ins_product("p1", "life_insurance", 33, 410, 137)]
    v = {"now": "2026-10-21T10:00", "acct_products_json": json.dumps(prods)}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    st = json.loads(v["mp_state"])
    assert "2026-10-28" in json.dumps(st["p"]["p1"]) and "Wednesday 28 October 2026" in json.dumps(st["p"]["p1"]), st["p"]["p1"]
    assert [p for p in lint(w, s) if not p.startswith("warning:")] == []

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


# ---------------------------------------------------------------- 4. an event the account data fires (lapsed policy)

def _ins_lapsed():
    s = copy.deepcopy(INS)
    s["bands"][2]["event"] = "policy_lapsed"
    return s


def _prepare(w, prods, now="2026-10-29T10:00"):
    v = {"now": now, "acct_products_json": json.dumps(prods)}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    return v


def _route(node, v):
    """The first transition of a code node whose condition holds (equals / and / or only)."""
    def ok(c):
        if "logic" in c:
            r = [ok(x) for x in c["conditions"]]
            return all(r) if c["logic"] == "and" else any(r)
        return c["operator"] == "equals" and str(v.get(c["variable"])) == c["operand"]
    for t in node["transitions"]:
        if ok(t["condition"]):
            return t["target_node"]
    return node.get("passthrough_default_transition")


def test_band_event_validates():
    from builder.validate import validate
    assert validate(_ins_lapsed(), INS_OA) == []
    bad = _ins_lapsed()
    bad["bands"][2]["event"] = "nope"
    assert any("nope" in e for e in validate(bad, INS_OA))


def test_in06_lapsed_policy_fires_before_the_debt_is_stated():
    """IN-06: life policy 74 days overdue. Expected transferred/policy_lapsed to retention; the agent stated the premium,
    asked for payment and later transferred to customer_care as human_request."""
    s = _ins_lapsed()
    w = C(s, INS_OA, base_url="https://bank.example/v1")
    assert [p for p in lint(w, s) if not p.startswith("warning:")] == []
    v = _prepare(w, [_ins_product("p1", "life_insurance", 74, 96, 32)])
    assert v["account_event"] == "policy_lapsed" and v["product_words"] == "life insurance policy ending 3307"
    first = _route(w["nodes"]["prepare_account"], v)
    assert first == "policy_lapsed_offer"
    hold = w["nodes"]["policy_lapsed_hold"]
    assert "sympathy" not in hold["task_message"] and "Thank you, you are verified." in hold["task_message"]
    assert "has lapsed, so its cover has stopped" in hold["task_message"]
    tr = {t["description"]: t["target_node"] for t in hold["transitions"]}
    acc = [t for d, t in tr.items() if "transfer accepted" in d][0]
    assert w["nodes"][acc]["pre_actions"][0]["function"]["parameters"]["properties"]["body"]["properties"]["queue"]["default"] == "retention"
    close_t = w["nodes"][acc]["transitions"][0]["target_node"]  # A24: the transfer's ok route (otherwise its retry)
    assert _close_of(w["nodes"][close_t]) == ("transferred", "policy_lapsed")
    # declined, or a payment named: the else-close straight away, no wrap and so no notes
    assert tr["policy_lapsed: the customer names a payment, none is taken"] == "close_policy_lapsed"
    assert _close_of(w["nodes"]["close_policy_lapsed"]) == ("no_promise", "policy_lapsed")
    assert "policy_lapsed_back" not in w["nodes"]
    # never listened for in speech
    neg = w["nodes"]["negotiate"]["extraction_schema"][0]["enum_values"]
    assert not any(x.startswith("policy_lapsed") for x in neg)


def test_lapsed_second_policy_names_that_policy():
    s = _ins_lapsed()
    w = C(s, INS_OA, base_url="https://bank.example/v1")
    v = _prepare(w, [_ins_product("p1", "car_insurance", 9), dict(_ins_product("p2", "home_insurance", 70, 615, 205), policy_number_last4="5073")])
    assert v["account_event"] == "policy_lapsed" and v["product_words"] == "home insurance policy ending 5073"


def test_self_test_case_for_an_account_event_uses_that_band():
    from builder import cases
    c = cases.build(_ins_lapsed(), INS_OA, "event:policy_lapsed", "2026-10-29T10:00")
    assert c["products"][0]["days_overdue"] > 60
    assert c["expected"]["outcome"] == "no_promise" and c["expected"]["reason"] == "policy_lapsed" and c["expected"]["notes"] == []
    assert "Declines the offer of a transfer" in c["script"]


def test_ids08_tomorrow_holiday_is_named_outside_the_window():
    """IDS-08: Tue 10 Nov 16:30; tomorrow (Wed 11 Nov) is a holiday. The agent booked Thu 12 Nov without saying so."""
    w = C(IDS, IDS_OA, base_url="https://bank.example/v1")
    v = {"now": "2026-11-10T16:30"}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    assert "Tomorrow, Wednesday 11 November 2026 (2026-11-11), is not a business day" in v["callback_rules"], v["callback_rules"]
    assert "Thursday 12 November 2026" in v["callback_rules"]
    assert "Never move the day or time they name" in w["nodes"]["third_party"]["task_message"]
    v = {"now": "2026-11-09T16:30"}   # Monday: tomorrow is the next business day, no note
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    assert "is not a business day" not in v["callback_rules"]


def _renamed(spec, names):
    """The spec and its openapi with outcome names renamed ({old: new})."""
    s = copy.deepcopy(spec)
    for e in s["events"]:
        for k in ("outcome", "else_outcome"):
            if e.get(k) in names:
                e[k] = names[e[k]]
    for o in s["outcomes"]:
        o["outcome"] = names.get(o["outcome"], o["outcome"])
    txt = json.dumps(INS_OA)
    for a, b in names.items():
        txt = txt.replace('"%s"' % a, '"%s"' % b)
    return s, json.loads(txt)


def test_else_close_and_cases_take_outcome_names_from_the_spec():
    """Fix round 1 (M-1): no literal no_promise / transferred in the else-close or the self-test expectations."""
    from builder import cases
    s, oa = _renamed(INS, {"no_promise": "unpaid", "transferred": "handed_over"})
    # an else-close without else_outcome falls back to the refused event's outcome
    for e in s["events"]:
        if e["id"] == "hardship":
            e["else_outcome"] = None
    w = C(s, oa, base_url="https://bank.example/v1")
    assert _close_of(w["nodes"]["close_hardship"])[0] == "unpaid"
    assert cases._note_row(s, "hardship") == ("unpaid", "hardship")
    assert cases._note_row(s, "policy_lapsed") == ("unpaid", "policy_lapsed")
    exp = cases._multi_expected(s, [{"product_id": "p1", "type": "car_insurance", "premium_due": 100, "days_overdue": 5}], {"p1": "refused"})
    assert (exp["outcome"], exp["reason"]) == ("unpaid", "refused")


CORPUS_ONLY = ["lapse", "premium", "brightwater", "harbourline", "northwind", "retention", "insurance", "reinstat",
               "policyholder", "5th business day"]


def test_no_corpus_words_in_generic_text():
    """Fix round 1 (I-1): words only the bench corpus uses never appear in the schema, the guide, the prompts or the code."""
    import glob
    files = ["builder/spec_schema.json", "docs/spec-guide.md"] + glob.glob("builder/prompts/*.md") + \
        glob.glob("builder/*.py") + glob.glob("builder/lib/*.py")
    hits = []
    for f in files:
        text = open(f).read().lower()
        hits += ["%s: %s" % (f, w) for w in CORPUS_ONLY if w in text]
    assert hits == []


def test_no_lapsed_policy_negotiates_as_before():
    s = _ins_lapsed()
    w = C(s, INS_OA, base_url="https://bank.example/v1")
    v = _prepare(w, [_ins_product("p1", "car_insurance", 9)])
    assert v["account_event"] == "none" and _route(w["nodes"]["prepare_account"], v) == "negotiate"

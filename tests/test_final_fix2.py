"""Second whole-branch review fixes (final-review-2.md): I1-I5 and M1. Offline only."""
import copy
import json

from builder.compile import compile as C
from builder.lint import lint
from tests.test_a10 import _code

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
INS = json.load(open("tests/fixtures/bench-insurance.spec.json"))
INS_OA = json.load(open("bench/insurance/openapi.json"))
URL = "https://bank.example/v1"


def _errors(w, s):
    return [e for e in lint(w, s) if not e.startswith("warning:")]


# ---------------------------------------------------------------- I1: a placeholder in the first sentence

def test_i1_first_sentence_placeholder_is_rendered():
    for base in (A, B):
        s = copy.deepcopy(base)
        s["opening"]["first_sentence"] = "Hello {first_name}, this is a digital assistant calling from the bank."
        w = C(s, OA, base_url=URL)
        t = w["nodes"]["opening"]["on_enter"]["text"]
        assert t.startswith("Hello {{first_name}}, this is")
        assert "{first_name}" not in t.replace("{{first_name}}", "")
        assert _errors(w, s) == []


def test_i1_verbatim_first_sentence_with_placeholder_stays_exempt():
    s = copy.deepcopy(INS)
    s["opening"]["verbatim"] = True
    s["opening"]["first_sentence"] = "Hello {first_name}, this is Brightwater. This call is recorded for quality."
    w = C(s, INS_OA, base_url=URL)
    assert w["nodes"]["opening"]["on_enter"]["text"].startswith(
        "Hello {{first_name}}, this is Brightwater. This call is recorded for quality. ")
    errs = lint(w, s)
    assert [e for e in errs if not e.startswith("warning:")] == []
    assert any(e.startswith("warning:") and "recorded" in e for e in errs)


def test_i1_lint_flags_a_raw_placeholder_in_spoken_text():
    w = C(A, OA, base_url=URL)
    w["nodes"]["opening"]["on_enter"]["text"] = "Hello {first_name}. " + w["nodes"]["opening"]["on_enter"]["text"]
    assert any("raw placeholder {first_name}" in e for e in lint(w, A))


def test_i1_reference_specs_have_no_raw_placeholder():
    for s in (A, B):
        assert not any("raw placeholder" in e for e in lint(C(s, OA, base_url=URL), s))


# ---------------------------------------------------------------- I2: events[].stage_say in every stage

ID_SAY = "I am sorry, I cannot check your answers right now."
NEG_SAY = "I am sorry, I cannot save that arrangement right now."


def _farewell(node, v=None):
    v = dict(v or {"title": "Ms", "last_name": "Novak"})
    exec(_code(node), {"vars": v})
    return v["farewell"]


def _fail_spec(base, stages=("identity", "negotiate"), stage_say=None, say=None):
    s = copy.deepcopy(base)
    fe = next(e for e in s["events"] if e["id"] == s["confirmation"]["failure_event"])
    fe["stages"], fe["say"] = list(stages), say
    fe["stage_say"] = stage_say if stage_say is not None else {"identity": ID_SAY, "negotiate": NEG_SAY}
    return s


def test_i2_negotiate_failure_close_speaks_its_stage_sentence():
    from builder.compile import ID_FAIL_CLOSE
    s = _fail_spec(A)
    w = C(s, OA, base_url=URL)
    neg = w["nodes"]["negotiate"]
    fail = next(t["target_node"] for t in neg["transitions"] if t.get("description") == "Promise failed after the retries")
    said = _farewell(w["nodes"][fail])
    assert NEG_SAY in said and ID_SAY not in said
    assert fail == "close_service_error_negotiate"
    assert fail in json.dumps(w["nodes"]["retry_promise"])
    assert ID_SAY in _farewell(w["nodes"][ID_FAIL_CLOSE])
    assert _errors(w, s) == []


def test_i2_single_stage_event_uses_its_stage_sentence_in_the_default_close():
    s = _fail_spec(A, stages=("negotiate",), stage_say={"negotiate": NEG_SAY}, say="I could not record your promise.")
    w = C(s, OA, base_url=URL)
    said = _farewell(w["nodes"]["close_service_error"])
    assert NEG_SAY in said and "could not record" not in said
    assert not any(k.startswith("close_service_error_") for k in w["nodes"])


def test_i2_opening_and_third_party_sentences_get_their_own_closes():
    s = copy.deepcopy(A)
    ev = next(e for e in s["events"] if e["id"] == "deceased")
    ev["stage_say"] = {"opening": "My condolences.", "third_party": "I am very sorry to hear that."}
    w = C(s, OA, base_url=URL)
    n = w["nodes"]
    op = {t["target_node"] for t in n["opening"]["transitions"]}
    tp = {t["target_node"] for t in n["third_party"]["transitions"]}
    assert "close_deceased_opening" in op and "close_deceased_third_party" in tp
    assert "My condolences." in _farewell(n["close_deceased_opening"])
    assert "I am very sorry to hear that." in _farewell(n["close_deceased_third_party"])
    assert _errors(w, s) == []


def test_i2_insist_once_ask_uses_the_stage_sentence():
    s = copy.deepcopy(A)
    ev = next(e for e in s["events"] if e["id"] == "human")
    ev["stage_say"] = {"negotiate": "May I try to help with the payment first?"}
    n = C(s, OA, base_url=URL)["nodes"]
    assert n["human_hold_negotiate"]["on_enter"]["text"] == "May I try to help with the payment first?"
    assert n["human_hold_identity"]["on_enter"]["text"] == "Can I help you with that first?"


def test_i2_multi_wrap_fail_say_uses_negotiate_sentence():
    s = _fail_spec(B, stages=("negotiate",), stage_say={"negotiate": NEG_SAY}, say="I could not record your promise.")
    w = C(s, OA, base_url=URL)
    wraps = [k for k in w["nodes"] if k.startswith("wrap_")]
    assert wraps and all("FAIL_SAY = %r" % NEG_SAY in _code(w["nodes"][k]) for k in wraps)
    assert _errors(w, s) == []


def test_i2_cases_check_the_stage_sentence():
    from builder import cases
    s = _fail_spec(A)
    case = cases.build(s, OA, "service_error", "2026-10-14T11:00")
    assert 'the agent says "%s"' % NEG_SAY in case["checks"]
    assert NEG_SAY in cases.fixed_sentences(s) and ID_SAY in cases.fixed_sentences(s)
    s = copy.deepcopy(A)
    next(e for e in s["events"] if e["id"] == "deceased")["stage_say"] = {"third_party": "I am very sorry to hear that."}
    case = cases.build(s, OA, "event:deceased", "2026-10-14T11:00")
    assert 'the agent says "I am very sorry to hear that."' in case["checks"]


# ---------------------------------------------------------------- I3: same_day_cutoff in the plan, multi and cases

LOAN = {"product_id": "p1", "type": "personal_loan", "past_due": 540.0, "days_late": 5, "instalment_amount": 180.0}


def _cut(base, cutoff="18:00"):
    s = copy.deepcopy(base)
    s["negotiation"]["same_day_cutoff"] = cutoff
    for b in s["bands"]:
        if b["attempts"]:
            b["attempts"][0] = dict(b["attempts"][0], date="today")
    return s


def _account(spec, now, product=LOAN):
    w = C(spec, OA)
    v = {"now": now, "title": "Mr", "last_name": "Ortiz", "acct_first_json": json.dumps(product)}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    assert v["account_state"] == "ok", v.get("account_error")
    return w, v


def test_i3_single_plan_moves_today_after_the_cutoff():
    s = _cut(A)
    w, v = _account(s, "2026-10-14T19:30")
    assert v["offer_dates"].split(",")[0] == "2026-10-15"
    assert "2026-10-14" not in v["allowed_dates_text"]
    assert v["attempt1_text"].startswith("Thursday 15 October")
    assert "too late for a payment today" in v["calendar_text"]
    assert v["pay_day_text"] == "on Thursday 15 October 2026"
    first = w["nodes"]["negotiate"]["on_enter"]["text"]
    assert "{{pay_day_text}}" in first and "today" not in first.split("{{debt_statement_text}}")[1]
    assert "{{pay_day_text}}" in w["nodes"]["negotiate"]["task_message"]
    # before the cut-off nothing moves
    w, v = _account(s, "2026-10-14T11:00")
    assert v["offer_dates"].split(",")[0] == "2026-10-14" and "2026-10-14" in v["allowed_dates_text"]
    assert v["pay_day_text"] == "today" and "too late" not in v["calendar_text"]
    assert _errors(w, s) == []


def test_i3_cutoff_code_has_no_brace_pairs_and_reference_specs_have_none():
    w = C(_cut(A), OA)
    code = _code(w["nodes"]["prepare_account"])
    assert "{{" not in code and "}}" not in code and "TODAY_OPEN" in code
    for spec in (A, B):
        assert "TODAY_OPEN" not in _code(C(spec, OA)["nodes"]["prepare_account"])


class _MultiSim:
    def __init__(self, spec, now):
        from tests.test_multi import CASES, _products, _tool as tool
        self.w = C(spec, OA, base_url=URL)
        self.v = dict(CASES["PUB-11"]["variables"], now=now, customer_id="c1",
                      acct_products_json=json.dumps(_products("PUB-11")))
        for n in ("setup", "prepare_account"):
            exec(_code(self.w["nodes"][n]), {"vars": self.v})
        assert self.v["account_state"] == "ok", self.v.get("account_error")
        self.t = tool(self.w["nodes"]["negotiate"], "set_product")

    def say(self, **body):
        self.v["negotiate.total_turns"] = int(self.v.get("negotiate.total_turns") or 0) + 1
        cfg = {"url": "x", "body": dict(body)}
        exec(self.t["pre_process_code"], {"vars": self.v, "config": cfg})
        r = {"status": "success", "data": {"ok": True, "products": []}, "header": {}}
        exec(self.t["post_process_code"], {"vars": self.v, "result": r})
        return self.v["mp_msg"]


def test_i3_multi_rejects_today_after_the_cutoff():
    s = copy.deepcopy(B)
    s["negotiation"]["same_day_cutoff"] = "18:00"
    m = _MultiSim(s, "2026-10-14T19:30")
    msg = m.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-14")
    assert "too late for a payment today" in msg and "Thursday 15 October" in msg
    assert m.v["mp_phase"] == "open"
    assert "today" not in m.v["first_ask_text"] and "on Thursday 15 October 2026" in m.v["first_ask_text"]
    m.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-15")
    assert m.v["mp_phase"] == "readback"
    # before the cut-off today is accepted
    m = _MultiSim(s, "2026-10-14T11:00")
    m.say(product_id="ALL", outcome="agreed", amount="full", date="2026-10-14")
    assert m.v["mp_phase"] == "readback"
    assert _errors(m.w, s) == []


def test_i3_cases_expect_the_next_business_day_after_the_cutoff():
    from builder import cases
    s = _cut(A)
    late = cases.build(s, OA, "happy_full_today", "2026-10-14T19:30", products=[LOAN])
    early = cases.build(s, OA, "happy_full_today", "2026-10-14T11:00", products=[LOAN])
    assert "2026-10-15" in json.dumps(late["expected"]) and "2026-10-14" not in json.dumps(late["expected"])
    assert "2026-10-14" in json.dumps(early["expected"])
    fl = cases.build(s, OA, "floor_after_too_much", "2026-10-14T19:30", products=[LOAN])
    assert fl and not any("14 October" in c for c in fl["checks"])


# ---------------------------------------------------------------- I4: relative phrases name no day

def test_i4_relative_phrases_resolve_to_no_day():
    import datetime as dt
    from builder.lib.dates import resolve_said
    t = dt.date(2026, 10, 14)
    for said in ("two weeks from today", "a week today", "within a week from today", "three days from today",
                 "the day after the 20th", "sometime after the 20th", "before Friday", "after Friday",
                 "a week from the 20th", "two days after Friday", "not before Friday"):
        assert resolve_said(said, t) == (None, "none"), said
    # plain days and the phrases the module does resolve are unchanged
    for said, d in (("today", t), ("the 20th", dt.date(2026, 10, 20)), ("Friday", dt.date(2026, 10, 16)),
                    ("from Friday", dt.date(2026, 10, 16)), ("on the 20th", dt.date(2026, 10, 20)),
                    ("the day after tomorrow", dt.date(2026, 10, 16)), ("the Friday after next", dt.date(2026, 10, 30)),
                    ("after the 20th, no, the 23rd", dt.date(2026, 10, 23))):
        assert resolve_said(said, t) == (d, ""), said


def test_i4_check_date_uses_the_models_date_for_a_relative_phrase():
    from tests.test_a12 import account, check
    w, v = account("2026-10-14T11:00")
    d = check(w, v, "two weeks from today", date="2026-10-28")
    assert d["date"] == "2026-10-28" and "14 October" not in json.dumps(d)
    d = check(w, v, "the day after the 20th", date="2026-10-21")
    assert d["date"] == "2026-10-21"


# ---------------------------------------------------------------- I5: no invented call-variable samples

def test_i5_test_variables_invent_nothing_and_leave_the_clock_real():
    import datetime as dt
    s = copy.deepcopy(A)
    s["call_variables"] += ["company_name", "policy_number"]
    w = C(s, OA)
    tv = w["test_variables"]
    assert tv == {"customer_id": "c_test", "first_name": "", "last_name": "", "title": "", "company_name": "",
                  "policy_number": ""}
    v = dict(tv)                       # a dashboard call: only the declared variables
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    assert v["setup_state"] == "ok" and v["now_time"]
    from builder.lib.dates import speak
    assert v["today_text"] in (speak(dt.date.today()), speak(dt.datetime.now().date()))


# ---------------------------------------------------------------- minors

def test_m3_an_outcome_with_a_reasoned_row_keeps_its_reason():
    s = copy.deepcopy(A)
    row = next(o for o in s["outcomes"] if o["outcome"] == "no_promise" and o.get("reason"))
    s["outcomes"].append(dict(row, reason=None))
    pre = next(t for t in C(s, OA)["tools"] if t["function"]["name"] == "close_call")["pre_process_code"]
    line = next(x for x in pre.splitlines() if x.startswith("REASONLESS"))
    assert "no_promise" not in line and "promise" in line


def test_m8_multi_default_failure_sentence_says_no_record_word():
    from builder.multi import FAIL_SAY_DEFAULT
    assert "record" not in FAIL_SAY_DEFAULT.lower()


def test_i2_reference_specs_have_no_stage_variants():
    stages = ("_opening", "_identity", "_negotiate", "_third_party")
    for s in (A, B):
        n = C(s, OA, base_url=URL)["nodes"]
        assert not [k for k in n if k.startswith("close_") and k.endswith(stages)]

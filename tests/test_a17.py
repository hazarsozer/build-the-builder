"""Task A17: bench sme gaps. Fixture: the bench run's extracted spec (tests/fixtures/bench-sme.spec.json).

SM-05/06: a one-product brief that notes only one event is compiled as several products; every wrap whose own close
was not a note's close fell to the last note close (in_administration). SM-03: an HTTP error from verify_identity
skips the post-hook, nothing told the model to try again, and the service-failure close was listened for in the
customer's words."""
import ast
import copy
import glob
import json
import re

from builder.compile import ID_FAIL_CLOSE, compile as C
from builder.lint import lint

SME = json.load(open("tests/fixtures/bench-sme.spec.json"))
SME_OA = json.load(open("bench/sme/openapi.json"))
INS = json.load(open("tests/fixtures/bench-insurance.spec.json"))
INS_OA = json.load(open("bench/insurance/openapi.json"))
OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
URL = "https://bank.example/v1"


def _code(node):
    return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def _close_of(node):
    c = _code(node)
    return re.search(r"OUTCOME = '([^']*)'", c).group(1), re.search(r"REASON = '([^']*)'", c).group(1) or None


def _holds(c, v):
    if c.get("operator") == "always":
        return True
    if "logic" in c:
        r = [_holds(x, v) for x in c["conditions"]]
        return all(r) if c["logic"] == "and" else any(r)
    val = v.get(c["variable"])
    if c["operator"] == "equals":
        return str(val) == str(c["operand"])
    if c["operator"] == "greater_than":
        try:
            return float(val) > float(c["operand"])
        except (TypeError, ValueError):
            return False
    raise AssertionError(c)


def _route(node, v):
    """The first engine transition that holds (tool-call transitions are the model's), else None."""
    for t in node["transitions"]:
        if not t.get("is_tool_call") and _holds(t["condition"], v):
            return t["target_node"]
    return None


def _errors(w, spec):
    """Lint errors, minus the one the base already had for this fixture (its change note says "recorded" in negotiate)."""
    return [e for e in lint(w, spec) if not e.startswith("warning") and "cannot be changed" not in e]


def _product(days_late=12, past_due=3450.0, floor=690.0):
    return {"product_id": "p1", "type": "business_overdraft", "account_last4": "6620", "past_due": past_due,
            "days_late": days_late, "minimum_due": floor}


def _wrap(w, node, status, confirmed=False, recorded=False, now="2026-10-22T16:00"):
    """Run setup, prepare_account, the given product status and the wrap; follow notes and finish to a close."""
    v = {"now": now, "acct_products_json": json.dumps([_product()])}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    st = json.loads(v["mp_state"])
    st["p"]["p1"]["status"] = status
    if status == "agreed":
        st["p"]["p1"].update(amount=3450.0, date="2026-10-23")
    v["mp_state"] = json.dumps(st)
    if confirmed:
        v["mp_confirmed"] = json.dumps(["p1"])
    if node == "wrap_recorded":
        exec(_code(w["nodes"]["record_plan"]), {"vars": v})
        v["rec1_status"] = "recorded" if recorded else ""
    exec(_code(w["nodes"][node]), {"vars": v})
    notes, cur = [], _route(w["nodes"][node], v) or w["nodes"][node]["passthrough_default_transition"]
    while cur.startswith("note_"):
        notes.append((v["note%s_pid" % cur[-1]], v["note%s_reason" % cur[-1]]))
        cur = _route(w["nodes"][cur], v)
    assert cur == "finish"
    close = _route(w["nodes"]["finish"], v)
    exec(_code(w["nodes"][close]), {"vars": v})
    return close, _close_of(w["nodes"][close]), notes, v


# ---------------------------------------------------------------- SM-05 / SM-06: the wrap's own close

def test_sme_compiles_as_several_products_and_lints_clean():
    w = C(SME, SME_OA, base_url=URL)
    assert "wrap_done" in w["nodes"] and "finish" in w["nodes"]
    assert _errors(w, SME) == []


def test_sm05_refused_every_attempt_closes_refused_not_the_noted_event():
    w = C(SME, SME_OA, base_url=URL)
    close, out, notes, v = _wrap(w, "wrap_done", "refused")
    assert out == ("no_promise", "refused") and notes == [], (close, out, notes)
    said = v["farewell"].lower()
    assert "administrat" not in said


def test_sm06_open_product_at_the_end_closes_refused():
    w = C(SME, SME_OA, base_url=URL)
    close, out, notes, _ = _wrap(w, "wrap_done", "pending", now="2026-10-19T10:00")
    assert out == ("no_promise", "refused") and notes == []


def test_sm08_administration_still_noted_and_closed_as_such():
    w = C(SME, SME_OA, base_url=URL)
    close, out, notes, _ = _wrap(w, "wrap_in_administration", "administration", now="2026-11-10T10:00")
    assert out == ("no_promise", "in_administration") and notes == [("p1", "administration")]


def test_every_sme_wrap_closes_with_its_own_event():
    w = C(SME, SME_OA, base_url=URL)
    want = {"wrap_dispute": ("no_promise", "dispute"), "wrap_hardship": ("no_promise", "hardship"),
            "wrap_not_talk": ("no_promise", "refused"), "wrap_paid": ("paid", None), "wrap_done": ("no_promise", "refused")}
    for node, exp in want.items():
        _, out, notes, _ = _wrap(w, node, "pending")
        assert out == exp and notes == [], (node, out)


def test_sme_failed_promise_closes_as_the_service_failure_and_says_so():
    w = C(SME, SME_OA, base_url=URL)
    close, out, notes, v = _wrap(w, "wrap_recorded", "agreed", confirmed=True, recorded=False)
    assert out == ("no_promise", "service_error") and notes == []
    assert v["farewell"].startswith("I am sorry, I could not record your promise. This concerns your business")
    close, out, _, v = _wrap(w, "wrap_recorded", "agreed", confirmed=True, recorded=True)
    assert out == ("promise", None)


def test_every_wrap_falls_back_to_its_own_close_when_the_table_has_it():
    """Generic: in every several-product spec, a wrap whose close no note decides goes to its own fallback's close."""
    for spec, oa in ((SME, SME_OA), (INS, INS_OA), (B, OA)):
        w = C(spec, oa, base_url=URL)
        by = {}
        for o in spec["outcomes"]:
            by.setdefault("%s|%s" % (o["outcome"], o.get("reason") or ""), "close_" + o["event"])
        finish = {t["target_node"] for t in w["nodes"]["finish"]["transitions"]}
        for name, n in w["nodes"].items():
            if not name.startswith("wrap_") or n["node_type"] != "code":
                continue
            c = _code(n)
            fb = json.loads(ast.literal_eval(re.search(r"FALLBACK = json\.loads\((.*)\)\n", c).group(1)))
            close_by = json.loads(ast.literal_eval(re.search(r"CLOSE_BY = json\.loads\((.*)\)\n", c).group(1)))
            key = "%s|%s" % (fb[0], fb[1] or "")
            if key in by:
                assert close_by["*"] == by[key], (spec["bank_name"], name, close_by["*"], by[key])
                assert by[key] in finish


# ---------------------------------------------------------------- SM-06: two wrong answers on the same question

def _verify(w, v, field, answer, result):
    t = w["nodes"]["identity"]["tools"][0]
    cfg = {"url": "x", "body": {"field": field, "answer": answer}}
    exec(t["pre_process_code"], {"vars": v, "config": cfg})
    if result.get("status") == "error":  # an HTTP error: the platform skips the post-hook
        v["verify_identity_response.status"] = "error"
        v["verify_identity_failure_count"] = v.get("verify_identity_failure_count", 0) + 1
        return None
    r = copy.deepcopy(result)
    exec(t["post_process_code"], {"vars": v, "result": r})
    v["verify_identity_response.status"] = r.get("status", "success")
    for m in t["response_mapping"]:
        v[m["variable"]] = r["data"].get(m["path"].split(".", 1)[1])
    for k, x in r["data"].items():
        v["verify_identity_response.data." + k] = x
    return r["data"]


def test_sm06_second_wrong_year_ends_the_check_even_if_the_bank_allows_more():
    w = C(SME, SME_OA, base_url=URL)
    v = {"now": "2026-10-19T10:00"}
    idn = w["nodes"]["identity"]
    _verify(w, v, "company_number", "07315842", {"status": "success", "data": {"ok": True, "verified": True, "wrong_answers_left": 3, "all_verified": False}})
    assert _route(idn, v) is None
    d = _verify(w, v, "founded_year", "2001", {"status": "success", "data": {"ok": True, "verified": False, "wrong_answers_left": 2, "all_verified": False}})
    assert d["wrong_answers_left"] == 1 and _route(idn, v) is None
    d = _verify(w, v, "founded_year", "2012", {"status": "success", "data": {"ok": True, "verified": False, "wrong_answers_left": 1, "all_verified": False}})
    assert d["wrong_answers_left"] == 0 and _route(idn, v) == "close_failed"
    assert _close_of(w["nodes"]["close_failed"]) == ("not_verified", "failed")


# ---------------------------------------------------------------- SM-03: verify_identity fails once

def test_sm03_one_http_error_stays_in_identity_and_tells_the_model_to_retry():
    w = C(SME, SME_OA, base_url=URL)
    idn = w["nodes"]["identity"]
    assert "Call verify_identity again at once with the same field and answer" in idn["task_message"]
    assert "service_error" not in idn["extraction_schema"][0]["enum_values"]
    assert "still fails after one retry" not in idn["task_message"]  # the event's own line no longer reaches the model
    v = {"now": "2026-10-16T14:00"}
    assert _verify(w, v, "company_number", "08833104", {"status": "error"}) is None
    assert _route(idn, v) is None  # the model retries
    _verify(w, v, "company_number", "08833104", {"status": "success", "data": {"ok": True, "verified": True, "wrong_answers_left": 3, "all_verified": False}})
    assert _route(idn, v) is None
    _verify(w, v, "founded_year", "2009", {"status": "success", "data": {"ok": True, "verified": True, "wrong_answers_left": 3, "all_verified": True}})
    assert _route(idn, v) == "load_account"


def test_sm03_second_http_error_closes_as_the_service_failure():
    w = C(SME, SME_OA, base_url=URL)
    idn = w["nodes"]["identity"]
    v = {"now": "2026-10-16T14:00"}
    _verify(w, v, "company_number", "08833104", {"status": "error"})
    _verify(w, v, "company_number", "08833104", {"status": "error"})
    assert _route(idn, v) == ID_FAIL_CLOSE
    node = w["nodes"][ID_FAIL_CLOSE]
    assert _close_of(node) == ("no_promise", "service_error")
    assert "technical problem" in _code(node) and node["passthrough_default_transition"] == "goodbye"


def test_sm03_a_result_with_no_answer_check_counts_too():
    """A response that reaches the post-hook but checked nothing (no ok, no verified) is a technical failure too."""
    w = C(SME, SME_OA, base_url=URL)
    idn = w["nodes"]["identity"]
    v = {"now": "2026-10-16T14:00"}
    bad = {"status": "success", "data": {"error": "unavailable"}}
    d = _verify(w, v, "company_number", "08833104", bad)
    assert "technical problem" in d["what_to_do_next"] and _route(idn, v) is None
    _verify(w, v, "company_number", "08833104", bad)
    assert _route(idn, v) == ID_FAIL_CLOSE


def test_identity_failure_route_only_when_the_failure_event_covers_identity():
    for spec in (A, B):
        w = C(spec, OA)
        assert ID_FAIL_CLOSE not in w["nodes"]
        assert "verify_identity_failure_count" not in json.dumps(w["nodes"]["identity"]["transitions"])
        assert "id_tech_n" not in w["nodes"]["identity"]["tools"][0]["post_process_code"]
    s = copy.deepcopy(SME)
    for e in s["events"]:
        if e["id"] == "service_error":
            e["stages"] = ["negotiate"]
    assert ID_FAIL_CLOSE not in C(s, SME_OA, base_url=URL)["nodes"]


def test_reference_specs_lint_clean():
    assert lint(C(A, OA), A) == []
    assert lint(C(B, OA), B) == []


# ---------------------------------------------------------------- SM-01: a word-for-word opening

def test_sm01_verbatim_opening_is_spoken_exactly():
    s = copy.deepcopy(SME)
    s["opening"]["verbatim"] = True
    w = C(s, SME_OA, base_url=URL)
    assert w["nodes"]["opening"]["on_enter"]["text"].startswith(s["opening"]["first_sentence"] + " ")
    assert _errors(w, s) == []


def test_no_sme_corpus_words_in_generic_text():
    words = ["harbor", "overdraft", "administration", "insolvency", "founded", "company_number"]
    files = ["builder/spec_schema.json", "docs/spec-guide.md"] + glob.glob("builder/prompts/*.md") + \
        glob.glob("builder/*.py") + glob.glob("builder/lib/*.py")
    hits = ["%s: %s" % (f, x) for f in files for x in words if x in open(f).read().lower()]
    assert hits == []


# ---------------------------------------------------------------- fix round 1: the identity failure close's sentence

def _id_fail_said(spec):
    w = C(spec, SME_OA, base_url=URL)
    v = {"title": "Ms", "last_name": "Novak"}
    exec(_code(w["nodes"][ID_FAIL_CLOSE]), {"vars": v})
    return v["farewell"]


def test_identity_failure_close_speaks_the_events_identity_sentence():
    s = copy.deepcopy(SME)
    fe = next(e for e in s["events"] if e["id"] == "service_error")
    fe["stage_say"] = {"identity": "I am sorry, I cannot check your answers right now. We will call you back."}
    said = _id_fail_said(s)
    assert said.startswith("I am sorry, I cannot check your answers right now. We will call you back. ")
    assert "technical problem" not in said
    from builder.validate import validate
    assert validate(s, SME_OA) == []


def test_identity_failure_close_falls_back_to_say_then_generic():
    s = copy.deepcopy(SME)
    assert _id_fail_said(s).startswith("I am sorry, there was a technical problem. ")  # the fixture: no say, no stage_say
    fe = next(e for e in s["events"] if e["id"] == "service_error")
    fe["say"] = "Sorry, our service is down."
    assert _id_fail_said(s).startswith("Sorry, our service is down. ")

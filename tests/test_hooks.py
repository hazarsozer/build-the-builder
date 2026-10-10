"""Pre/post hook snippets must survive results and requests without the expected fields."""
import json

from builder.compile import compile as C

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))

RESULTS = [{}, {"status": "error"}, {"data": None}, {"status": "success", "data": {"ok": False}},
           {"status": "error", "data": {"ok": True}}, "not a dict", None]


def _tools(w):
    for k, n in w["nodes"].items():
        for t in n.get("tools", []) + n.get("pre_actions", []):
            yield k, t
    for t in w.get("tools", []):
        yield "workflow", t


def run(code, result, vars_=None, config=None):
    g = {"result": result, "vars": vars_ if vars_ is not None else {}, "config": config if config is not None else {}}
    exec(code, g)
    return g


def test_posthook_missing_fields():
    w = C(A, OA)
    seen = 0
    for k, t in _tools(w):
        code = t.get("post_process_code")
        if not code:
            continue
        seen += 1
        for r in RESULTS:
            r = json.loads(json.dumps(r))
            run(code, r)
            if isinstance(r, dict) and isinstance(r.get("data"), dict):
                ok = r.get("status", "success") == "success" and r["data"].get("ok") is True
                if r["data"].get("promise_status") == "recorded":
                    assert ok, (k, r)
    assert seen >= 4


def test_promise_posthook_records_on_ok():
    w = C(A, OA)
    code = [t for k, t in _tools(w)
            if t["function"]["name"] == "create_promise" and t.get("post_process_code")][0]["post_process_code"]
    r = {"status": "success", "data": {"ok": True, "promise_id": "x"}}
    run(code, r)
    assert r["data"]["promise_status"] == "recorded"
    v = {}
    r = {"status": "success", "data": {"ok": False}}
    run(code, r, v)
    assert r["data"]["promise_status"] == "failed" and "again" in r["data"]["what_to_do_next"]
    r = {"status": "success", "data": {"ok": False}}
    run(code, r, v)
    assert "Do not try again" in r["data"]["what_to_do_next"]


def test_prehooks_survive_bad_config():
    w = C(A, OA)
    for k, t in _tools(w):
        code = t.get("pre_process_code")
        if not code:
            continue
        for cfg in [{}, {"body": None}, {"body": {}}, {"body": {"field": "x", "answer": None}}]:
            run(code, None, {}, json.loads(json.dumps(cfg)))


def _tool(w, name):
    return [t for k, t in _tools(w) if t["function"]["name"] == name][0]


def test_verify_prehook_normalises_by_format():
    w = C(A, OA)
    pre = _tool(w, "verify_identity")["pre_process_code"]
    q = {x["format"]: x["field"] for x in A["identity"]["questions"]}
    cases = [(q["date"], "02/11/1979", "1979-11-02"), (q["date"], "14 March 1988", "1988-03-14"),
             (q["date"], "March 14, 1988", "1988-03-14"), (q["date"], "1988-03-14", "1988-03-14"),
             (q["digits"], "4 4 1 0", "4410")]
    for field, ans, want in cases:
        cfg = {"body": {"field": field, "answer": ans}}
        run(pre, None, {}, cfg)
        assert cfg["body"]["answer"] == want, (field, ans)


def _verify(w, field, answer, v, result):
    t = _tool(w, "verify_identity")
    cfg = {"body": {"field": field, "answer": answer}}
    run(t["pre_process_code"], None, v, cfg)
    if cfg["body"]["field"] == "incomplete_answer":
        result = {"status": "success", "data": {"ok": False, "error": "field must be ..."}}
    run(t["post_process_code"], result, v)
    return cfg["body"], result["data"]


def test_p1_partial_answers_never_reach_the_bank_as_answers():
    w = C(A, OA)
    q = {x["format"]: x["field"] for x in A["identity"]["questions"]}
    ok = {"status": "success", "data": {"ok": True, "verified": False, "wrong_answers_left": 2}}
    for field, ans in [(q["date"], "14 March"), (q["date"], "1988"), (q["date"], "March 1988"), (q["digits"], "44"),
                       (q["digits"], "4 4 7"), (q["digits"], "55 01 23 3 4 4 7")]:
        v = {}
        body, data = _verify(w, field, ans, v, dict(ok))
        assert body["field"] == "incomplete_answer" and body["answer"] == "", ans
        assert "INCOMPLETE" in data["what_to_do_next"] and "NOT a wrong answer" in data["what_to_do_next"]
        assert data["counted"] is False and v["id_incomplete"] == "no"


def test_p1_parts_are_combined_from_id_partial():
    w = C(A, OA)
    q = {x["format"]: x["field"] for x in A["identity"]["questions"]}
    body, _ = _verify(w, q["date"], "1988", {"id_partial": "14 March"}, {"status": "success", "data": {"ok": True}})
    assert body == {"field": q["date"], "answer": "1988-03-14"}
    body, _ = _verify(w, q["digits"], "10", {"id_partial": "44"}, {"status": "success", "data": {"ok": True}})
    assert body["answer"] == "4410"
    body, _ = _verify(w, q["digits"], "4410", {"id_partial": "99"}, {"status": "success", "data": {"ok": True}})
    assert body["answer"] == "4410"


OK = {"status": "success", "data": {"ok": True, "verified": True, "all_verified": False, "wrong_answers_left": 3}}
INCOMPLETE = None  # body rewritten to the incomplete_answer field, never checked by the bank
STALE_DOB = {"id_partial": "14 March 1988", "id_partial_spent": "14 March 1988"}

# (question format, model answer, vars before the call, answer sent to the bank or INCOMPLETE)
ROUND4 = [
    ("date", "1988", {"id_partial": "14 March"}, "1988-03-14"),
    ("date", "1988", {"id_partial": "14 March 1988"}, "1988-03-14"),
    ("date", "14 March", {}, INCOMPLETE),
    ("date", "1988", {}, INCOMPLETE),
    ("date", "03/14/1988", {}, "03/14/1988"),
    ("date", "the 14th of March, 1988", {}, "1988-03-14"),
    ("date", "fourteenth March 1988", {}, "fourteenth March 1988"),
    ("date", "1988", {"id_partial": "14 March", "id_partial_field": "phone_last4"}, INCOMPLETE),
    ("digits", "10", {"id_partial": "4410"}, INCOMPLETE),
    ("digits", "10", {"id_partial": "44"}, "4410"),
    ("digits", "10", {"id_partial": "44 10"}, "4410"),
    ("digits", "44", dict(STALE_DOB), INCOMPLETE),
    ("digits", "44", {"id_partial": "14 March 1988"}, INCOMPLETE),
    ("digits", "4401, sorry, I mean 4410", {}, "4410"),
    ("digits", "4401", {"id_partial": "44"}, "4401"),
    ("digits", "4 4 7", {}, INCOMPLETE),
    ("digits", "44", {"id_partial": "44"}, INCOMPLETE),
    ("digits", "44107", {}, INCOMPLETE),
    ("digits", "10", {"id_partial": "4401, sorry, I mean 44"}, INCOMPLETE),
]


def test_round4_completeness_table():
    w = C(A, OA)
    q = {x["format"]: x["field"] for x in A["identity"]["questions"]}
    for fm, ans, v, want in ROUND4:
        body, data = _verify(w, q[fm], ans, dict(v), json.loads(json.dumps(OK)))
        if want is INCOMPLETE:
            assert body == {"field": "incomplete_answer", "answer": ""}, (fm, ans, v, body)
            assert data["counted"] is False and "INCOMPLETE" in data["what_to_do_next"], (fm, ans, v)
        else:
            assert body == {"field": q[fm], "answer": want}, (fm, ans, v, body)


def test_round4_year_format_needs_exactly_four_digits():
    spec = json.loads(json.dumps(A))
    spec["identity"]["questions"][0] = {"field": "birth_year", "ask": "What year were you born?", "format": "year"}
    w = C(spec, OA)
    for ans, v, want in [("1988", {}, "1988"), ("88", {}, INCOMPLETE), ("19", {"id_partial": "19"}, INCOMPLETE),
                         ("88", {"id_partial": "19"}, "1988"), ("19888", {}, INCOMPLETE)]:
        body, _ = _verify(w, "birth_year", ans, dict(v), json.loads(json.dumps(OK)))
        assert body["answer"] == ("" if want is INCOMPLETE else want), (ans, v, body)


def test_round4_parts_are_spent_after_a_checked_call_and_kept_after_an_incomplete_one():
    w = C(A, OA)
    q = {x["format"]: x["field"] for x in A["identity"]["questions"]}
    v = {"id_partial": "14 March"}
    body, _ = _verify(w, q["date"], "14 March", v, OK)
    assert body["field"] == "incomplete_answer" and v["id_partial_field"] == q["date"]
    v["id_partial"] = "14 March 1988"
    body, _ = _verify(w, q["date"], "1988", v, json.loads(json.dumps(OK)))
    assert body["answer"] == "1988-03-14" and v["id_partial_spent"] == "14 March 1988" and v["id_partial_field"] == ""
    body, _ = _verify(w, q["digits"], "44", v, OK)  # the extractor still holds the DOB
    assert body["field"] == "incomplete_answer"
    v["id_partial"] = "44"
    body, _ = _verify(w, q["digits"], "10", v, OK)
    assert body["answer"] == "4410"


def test_round4_posthook_treats_field_errors_as_incomplete_without_the_flag():
    w = C(A, OA)
    post = _tool(w, "verify_identity")["post_process_code"]
    for r in ({"status": "success", "data": {"ok": False, "error": "field must be date_of_birth or phone_last4"}},
              {"status": "success", "error": "field must be date_of_birth or phone_last4", "data": {}}):
        v = {}
        run(post, r, v)
        assert r["data"]["counted"] is False and "INCOMPLETE" in r["data"]["what_to_do_next"]
        assert "id_partial_spent" not in v


def test_p3_self_correction_digits_keep_the_last_value():
    w = C(A, OA)
    q = {x["format"]: x["field"] for x in A["identity"]["questions"]}
    body, _ = _verify(w, q["digits"], "4401, sorry, I mean 4410", {}, {"status": "success", "data": {"ok": True}})
    assert body["answer"] == "4410"


def test_p1_p2_p3_prompt_and_extraction():
    n = C(A, OA)["nodes"]["identity"]
    assert "ask only for the missing part" in n["task_message"] and "corrected value" in n["task_message"]
    ans = n["tools"][0]["function"]["parameters"]["properties"]["body"]["properties"]["answer"]["description"]
    assert "combine parts said across turns" in ans and "corrected value" in ans
    names = [x["name"] for x in n["extraction_schema"]]
    assert "id_partial" in names
    assert "A partial answer" in n["extraction_schema"][0]["description"] and "never a refusal" in n["extraction_schema"][0]["description"]


def test_p4_combine_parts_in_negotiate_and_third_party():
    for spec in (A, json.load(open("specs/reference/spec-b.json"))):
        w = C(spec, OA)
        assert "combine parts said across turns" in w["nodes"]["negotiate"]["task_message"]
        assert "Combine parts said across turns" in w["nodes"]["third_party"]["task_message"]


def test_verify_posthook_messages():
    w = C(A, OA)
    post = _tool(w, "verify_identity")["post_process_code"]
    r = {"status": "success", "data": {"ok": True, "verified": True, "all_verified": False, "wrong_answers_left": 3}}
    run(post, r)
    assert A["identity"]["questions"][1]["ask"] in r["data"]["what_to_do_next"]
    r = {"status": "success", "data": {"ok": True, "verified": False, "hint": "day_month_swapped", "wrong_answers_left": 3}}
    run(post, r)
    assert "NOT a wrong answer" in r["data"]["what_to_do_next"]
    assert A["identity"]["non_counting_hints"]["day_month_swapped"] in r["data"]["what_to_do_next"]
    r = {"status": "success", "data": {"ok": True, "verified": False, "wrong_answers_left": 0}}
    run(post, r)
    assert "ended" in r["data"]["what_to_do_next"]
    r = {"status": "success", "data": {"ok": True, "verified": False, "wrong_answers_left": 2}}
    run(post, r)
    assert "SAME question" in r["data"]["what_to_do_next"]

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
    run(code, r, {"promise_sent": "yes"})  # A12: the date guard sent it to create_promise
    assert r["data"]["promise_status"] == "recorded"
    r = {"status": "success", "data": {"ok": True, "promise_id": "x"}}
    run(code, r, {})  # not sent by the guard (the request stayed on the read-only route): never recorded
    assert r["data"]["promise_status"] == "rejected"
    v = {"promise_sent": "yes"}
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
    body, _ = _verify(w, q["date"], "1988", {"id_partial": "14 March", "id_partial_field": q["date"]}, {"status": "success", "data": {"ok": True}})
    assert body == {"field": q["date"], "answer": "1988-03-14"}
    body, _ = _verify(w, q["digits"], "10", {"id_partial": "44", "id_partial_field": q["digits"]}, {"status": "success", "data": {"ok": True}})
    assert body["answer"] == "4410"
    body, _ = _verify(w, q["digits"], "4410", {"id_partial": "99", "id_partial_field": q["digits"]}, {"status": "success", "data": {"ok": True}})
    assert body["answer"] == "4410"


OK = {"status": "success", "data": {"ok": True, "verified": True, "all_verified": False, "wrong_answers_left": 3}}
INCOMPLETE = None  # body rewritten to the incomplete_answer field, never checked by the bank
DOB, PIN = "date_of_birth", "phone_last4"


def _p(part, field):
    """Vars as the extraction leaves them: the parts said so far and the question they answer."""
    return {"id_partial": part, "id_partial_field": field}


# (question format, model answer, vars before the call, answer sent to the bank or INCOMPLETE)
ROUND5 = [
    # self-corrections inside one answer: the last value counts
    ("date", "14 March 1988, sorry, 15 March 1988", {}, "1988-03-15"),
    ("date", "14 March 1988 no 15 March 1988", {}, "1988-03-15"),
    ("date", "14 March 1988, 15 March 1988", {}, INCOMPLETE),  # G7: two plain values, no correction word
    ("date", "14 March 1988, actually 15 March 1988", {}, "1988-03-15"),
    ("date", "14 March 1988, wait, 15 March 1988", {}, "1988-03-15"),
    ("date", "03/14/1988, sorry, 15/03/1988", {}, "1988-03-15"),
    ("date", "14 March 1988, sorry, 15", {}, INCOMPLETE),  # corrected only in part: never send the old date
    ("digits", "4401, sorry, I mean 4410", {}, "4410"),
    ("digits", "4401 4410", {}, INCOMPLETE),  # G7: two plain values, no correction word
    ("digits", "4410 and 5521", {}, INCOMPLETE),
    ("digits", "4401, no, 4410", {}, "4410"),
    ("year", "1987, no, 1988", {}, "1988"),
    # parts combined in both orders
    ("date", "1988", _p("14 March", DOB), "1988-03-14"),
    ("date", "14 March", _p("1988", DOB), "1988-03-14"),  # year said first
    ("date", "14", _p("March 1988", DOB), "1988-03-14"),
    ("date", "March", _p("14th 1988", DOB), "1988-03-14"),
    ("date", "1988", _p("14 March 1988", DOB), "1988-03-14"),
    ("digits", "10", _p("44", PIN), "4410"),
    ("digits", "03", _p("44", PIN), "4403"),
    ("digits", "10", _p("44 10", PIN), "4410"),
    ("year", "88", _p("19", "birth_year"), "1988"),
    # combinations that are not a whole answer stay incomplete
    ("date", "10", _p("14 March", DOB), INCOMPLETE),
    ("date", "14 March", _p("10", DOB), INCOMPLETE),
    ("date", "03", _p("44", DOB), INCOMPLETE),
    ("digits", "10", _p("4410", PIN), INCOMPLETE),
    ("digits", "44", _p("44", PIN), INCOMPLETE),
    ("digits", "10", _p("4401, sorry, I mean 44", PIN), INCOMPLETE),
    ("year", "19", _p("19", "birth_year"), INCOMPLETE),
    ("year", "19", _p("88", "birth_year"), INCOMPLETE),  # G7: 8819 is not a year (said order only)
    ("digits", "10", _p("14 March", PIN), INCOMPLETE),   # G7: a date part never becomes digits
    ("digits", "10", _p("March 44", PIN), INCOMPLETE),
    ("year", "2150", {}, INCOMPLETE),                    # G7: a year is 1900..now
    ("year", "1899", {}, INCOMPLETE),
    ("year", "1900", {}, "1900"),
    ("year", "2026", {}, "2026"),
    # number words
    ("date", "fourteenth March nineteen eighty-eight", {}, "1988-03-14"),
    ("date", "the fourteenth of March, nineteen eighty eight", {}, "1988-03-14"),
    ("date", "March the second, two thousand and four", {}, "2004-03-02"),
    ("date", "nineteen eighty-eight", _p("14 March", DOB), "1988-03-14"),
    ("date", "first of May nineteen oh four", {}, "1904-05-01"),
    ("digits", "four four one zero", {}, "4410"),
    ("digits", "forty-four ten", {}, "4410"),
    ("digits", "four four oh one", {}, "4401"),
    ("digits", "double four one oh", {}, "4410"),
    ("digits", "one zero", _p("four four", PIN), "4410"),
    ("year", "nineteen eighty-eight", {}, "1988"),
    ("year", "two thousand and four", {}, "2004"),
    ("year", "two thousand four", {}, "2004"),
    ("digits", "Oh, it is 4410", {}, "4410"),
    ("digits", "give me a second", {}, INCOMPLETE),
    # whole answers pass through
    ("date", "03/14/1988", {}, "03/14/1988"),  # the bank answers with the day_month_swapped hint
    ("date", "14/03/1988", {}, "1988-03-14"),
    ("date", "1988-03-14", {}, "1988-03-14"),
    ("date", "the 14th of March, 1988", {}, "1988-03-14"),
    ("date", "1988 March 14", {}, "1988-03-14"),
    ("digits", "4401", _p("44", PIN), "4401"),
    # partial answers alone
    ("date", "14 March", {}, INCOMPLETE),
    ("date", "1988", {}, INCOMPLETE),
    ("date", "March 1988", {}, INCOMPLETE),
    ("digits", "4 4 7", {}, INCOMPLETE),
    ("digits", "44107", {}, INCOMPLETE),
    # stale parts: another question, no question field, or already used by a checked call
    ("date", "1988", _p("14 March", PIN), INCOMPLETE),
    ("date", "1988", {"id_partial": "14 March"}, INCOMPLETE),
    ("digits", "44", _p("14 March 1988", PIN), INCOMPLETE),
    ("digits", "10", dict(_p("44", PIN), id_partial_spent="44"), INCOMPLETE),
    ("digits", "10", _p("44", DOB), INCOMPLETE),
]


def _w5():
    spec = json.loads(json.dumps(A))
    spec["identity"]["questions"].append({"field": "birth_year", "ask": "What year were you born?", "format": "year"})
    return C(spec, OA)


def test_round5_identity_table():
    assert len(ROUND5) >= 35
    w = _w5()
    q = {x["format"]: x["field"] for x in A["identity"]["questions"]}
    q["year"] = "birth_year"
    bad = []
    for fm, ans, v, want in ROUND5:
        body, data = _verify(w, q[fm], ans, dict(v), json.loads(json.dumps(OK)))
        if want is INCOMPLETE:
            if body != {"field": "incomplete_answer", "answer": ""} or data["counted"] is not False:
                bad.append((fm, ans, v, body))
        elif body != {"field": q[fm], "answer": want}:
            bad.append((fm, ans, v, body, want))
    assert not bad, bad


def test_round5_extraction_names_the_question_field():
    n = _w5()["nodes"]["identity"]
    ex = {x["name"]: x for x in n["extraction_schema"]}
    assert ex["id_partial_field"]["enum_values"] == [DOB, PIN, "birth_year"]


def test_round5_prehook_never_raises_on_odd_text():
    w = _w5()
    pre = _tool(w, "verify_identity")["pre_process_code"]
    assert "{{" not in pre and "}}" not in pre
    for line in pre.splitlines():
        if line.strip().startswith("import "):
            assert line.strip() in ("import json", "import re", "import datetime"), line
    odd = ["", " ", "-", "and", "oh", "double", "thousand", "hundred and", "nineteen", "ninety ninety ninety", "first",
           "second of", "31 February 1988", "99/99/9999", "0/0/0000", "sorry", "no no no", "1988 1988 1988", "a" * 500,
           "twenty-first of March two thousand", "three thousand thousand", "triple", "o o o", "1e9", "١٢٣٤"]
    for fld in (DOB, PIN, "birth_year"):
        for a in odd:
            for part in ("", "14 March", "44", "oh and"):
                cfg = {"body": {"field": fld, "answer": a}}
                run(pre, None, _p(part, fld), cfg)
                assert isinstance(cfg["body"]["answer"], str)


def test_round5_posthook_clears_parts_between_questions():
    w = C(A, OA)
    v = {}
    # Q1, the date of birth, said in two parts
    v.update(_p("14 March", DOB))
    body, _ = _verify(w, DOB, "14 March", v, json.loads(json.dumps(OK)))
    assert body["field"] == "incomplete_answer"
    assert v["id_partial"] == "14 March" and v["id_partial_field"] == DOB  # kept after INCOMPLETE
    v.update(_p("14 March 1988", DOB))
    body, _ = _verify(w, DOB, "1988", v, json.loads(json.dumps(OK)))
    assert body == {"field": DOB, "answer": "1988-03-14"}
    assert v["id_partial"] == "" and v["id_partial_field"] == "" and v["id_partial_spent"] == "14 March 1988"
    # Q2: the extraction lags and still reports the DOB parts, even under the new field
    v.update(_p("14 March 1988", PIN))
    body, _ = _verify(w, PIN, "10", v, json.loads(json.dumps(OK)))
    assert body["field"] == "incomplete_answer"
    v.update(_p("44", PIN))
    body, _ = _verify(w, PIN, "10", v, json.loads(json.dumps(OK)))
    assert body == {"field": PIN, "answer": "4410"}
    assert v["id_partial"] == ""
    # a technical error also ends the parts: the retry needs the whole answer again
    v.update(_p("44", PIN), id_partial_spent="")
    body, _ = _verify(w, PIN, "10", v, {"status": "error", "data": None})
    assert v["id_partial"] == "" and v["id_partial_spent"] == "44"
    body, _ = _verify(w, PIN, "10", dict(v, **_p("44", PIN)), json.loads(json.dumps(OK)))
    assert body["field"] == "incomplete_answer"


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

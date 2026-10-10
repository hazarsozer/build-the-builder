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
             (q["digits"], "55 01 23 3 4 4 7", "3447"), (q["digits"], "4 4 7", "447")]
    for field, ans, want in cases:
        cfg = {"body": {"field": field, "answer": ans}}
        run(pre, None, {}, cfg)
        assert cfg["body"]["answer"] == want, (field, ans)


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

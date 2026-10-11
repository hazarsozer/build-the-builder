"""Task A24: a generic tool-failure policy (bench edge-days-faults-3p, EDF-06..11).

Every bank tool call: an HTTP error -> exactly one retry the workflow performs (an engine node) or instructs at the
node that calls it; a second error -> the safe close: the failure event's (outcome, reason) (no_promise /
service_error on Brief A), a polite sentence, nothing claimed as recorded. A rule the brief gives itself wins
(create_promise: retry_promise / record+retry; a failure event that covers identity: its own sentence).

Offline engine replay as in test_a10/test_a17: code nodes run for real, an api_request node either succeeds (its
response mapping and <node>_response.data.* are set) or fails with an HTTP error (the platform skips the post-hook,
sets <name>_response.status = error and counts <name>_failure_count; no mapping is written)."""
import copy
import json
import re

from builder.compile import ID_FAIL_CLOSE, compile as C
from builder.lint import lint
from tests.test_a10 import _code, route

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
URL = "https://bank.example/v1"
FIXTURES = ["ids", "insurance", "sme", "outcomes", "strict-wording", "multi-raw"]
SPECS = [("spec-a", A, OA), ("spec-b", B, OA)] + [
    (f, json.load(open("tests/fixtures/bench-%s.spec.json" % f)), json.load(open("bench/%s/openapi.json" % f))) for f in FIXTURES]
BANK = {"get_account", "verify_identity", "create_promise", "schedule_callback", "transfer_to_agent", "add_note"}
CARD = {"product_id": "p1", "type": "credit_card", "card_last4": "4417", "past_due": 315.0, "days_late": 12, "minimum_payment": 63.0}
CLAIMS = re.compile(r"\b(?:recorded|registered|booked|scheduled|transferring|transferred|noted)\b", re.I)


def wf(spec, oa=OA):
    return C(spec, oa, base_url=URL)


def fail_close(spec):
    """The (outcome, reason) the safe close must write: the failure event's, else the refused event's outcome."""
    ev = {e["id"]: e for e in spec["events"]}
    fe = ev.get(spec["confirmation"].get("failure_event") or "") or {}
    return fe.get("outcome") or (ev.get("refused") or {}).get("outcome") or "no_promise", fe.get("reason") or None


def close_of(node):
    c = _code(node)
    return re.search(r"OUTCOME = '([^']*)'", c).group(1), re.search(r"REASON = '([^']*)'", c).group(1) or None


def farewell_of(node):
    return re.search(r"^T = (.*)$", _code(node), re.M).group(1)


def api_tools(node):
    return [t for t in (node.get("pre_actions") or []) + (node.get("tools") or [])
            if t.get("function", {}).get("handler") == "__function__:api_call"]


def tool_name(node):
    names = [t["function"]["name"] for t in api_tools(node)]
    return names[0] if names else None


def api(name, node, v, ok, data=None):
    """Run one api_request node: ok -> its mapping and response vars; not ok -> an HTTP error."""
    if not ok:
        v[name + "_response.status"] = "error"
        v[name + "_failure_count"] = int(v.get(name + "_failure_count") or 0) + 1
        return
    d = dict(data or {"ok": True})
    t = api_tools(node)[0]
    if t.get("post_process_code"):
        r = {"status": "success", "data": d}
        exec(t["post_process_code"], {"vars": v, "result": r, "json": json})
        d = r["data"]
    v[name + "_response.status"] = "success"
    for k, x in d.items():
        v[name + "_response.data." + k] = x
    for m in node.get("response_mapping") or []:
        val = d
        for p in m["path"].split(".")[1:]:
            val = val.get(p) if isinstance(val, dict) else None
        if val is not None:
            v[m["variable"]] = json.dumps(val) if isinstance(val, (list, dict)) else val


def walk(w, v, k, results):
    """Follow the engine from node k: code nodes run, api_request nodes take the next result (True/False/(True, data)).
    Stops at a conversation node or goodbye; returns (stop node, path)."""
    results = list(results)
    path = []
    while k and k != "goodbye":
        n = w["nodes"][k]
        path.append(k)
        if n["node_type"] == "code":
            exec(_code(n), {"vars": v})
        elif n["node_type"] == "api_request":
            r = results.pop(0)
            ok, data = r if isinstance(r, tuple) else (r, None)
            api(k, n, v, ok, data)
        else:
            break
        k = route(n, v)
    return k, path


def tool_error(v, tool):
    v[tool + "_response.status"] = "error"
    v[tool + "_failure_count"] = int(v.get(tool + "_failure_count") or 0) + 1


def started(w, now="2026-11-05T11:00"):
    v = {"customer_id": "c1", "first_name": "Tomas", "last_name": "Novak", "title": "Mr", "now": now}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    return v


# ---------------------------------------------------------------- generic property over 8 specs

def test_every_engine_bank_call_is_retried_once_by_a_node_that_calls_the_same_tool():
    for label, spec, oa in SPECS:
        w = wf(spec, oa)
        for name, n in w["nodes"].items():
            t = tool_name(n)
            if n["node_type"] != "api_request" or t not in BANK or "retry" in name:
                continue
            v = started(w)
            api(name, n, v, False)
            k = route(n, v)
            while w["nodes"][k]["node_type"] == "code" and not k.startswith("close_"):
                exec(_code(w["nodes"][k]), {"vars": v})
                k = route(w["nodes"][k], v)
            assert w["nodes"][k]["node_type"] == "api_request" and tool_name(w["nodes"][k]) == t, (label, name, k)
            assert "retry" in k, (label, name, k)


def test_a_second_get_account_or_transfer_failure_ends_in_the_service_failure_close():
    for label, spec, oa in SPECS:
        w = wf(spec, oa)
        want = fail_close(spec)
        starts = ["load_account"] + [k for k, n in w["nodes"].items()
                                     if tool_name(n) == "transfer_to_agent" and "retry" not in k]
        assert len(starts) > 1, label
        for s in starts:
            v = started(w)
            stop, path = walk(w, v, s, [False, False])
            close = [p for p in path if p.startswith("close_")][-1]
            assert stop == "goodbye" and close_of(w["nodes"][close]) == want, (label, s, path)
            assert sum(1 for p in path if w["nodes"][p]["node_type"] == "api_request") == 2, (label, s, path)
            assert not CLAIMS.search(farewell_of(w["nodes"][close])), (label, close)


def test_verify_identity_and_schedule_callback_retry_once_then_close_as_the_service_failure():
    for label, spec, oa in SPECS:
        w = wf(spec, oa)
        want = fail_close(spec)
        for node, tool in (("identity", "verify_identity"), ("third_party", "schedule_callback")):
            n = w["nodes"][node]
            assert re.search(r"If a %s call fails .*Call %s again at once" % (tool, tool), n["task_message"]), (label, node)
            v = started(w)
            tool_error(v, tool)
            assert route(n, v) is None, (label, node)          # the model calls it once more
            tool_error(v, tool)
            k = route(n, v)
            assert k and k.startswith("close_"), (label, node, k)
            assert close_of(w["nodes"][k]) == want, (label, node, k)
            assert not CLAIMS.search(farewell_of(w["nodes"][k])), (label, k)
            stop, path = walk(w, v, k, [])
            assert stop == "goodbye" and all(w["nodes"][p]["node_type"] == "code" for p in path), (label, path)


def test_service_failure_closes_record_nothing():
    """A failure close is a code node straight to goodbye: no bank call on the way, no claim in its farewell."""
    for label, spec, oa in SPECS:
        w = wf(spec, oa)
        for k in ("close_account_error", ID_FAIL_CLOSE, "close_callback_service_error", "close_transfer_service_error"):
            n = w["nodes"][k]
            assert n["node_type"] == "code" and api_tools(n) == [], (label, k)
            assert [t["target_node"] for t in n["transitions"]] == ["goodbye"], (label, k)
            assert "sorry" in farewell_of(n).lower() and not CLAIMS.search(farewell_of(n)), (label, k, farewell_of(n))


# ---------------------------------------------------------------- the EDF cases on Brief A

def test_edf06_get_account_fails_once_then_the_call_negotiates():
    w = wf(A)
    v = started(w)
    stop, path = walk(w, v, "load_account", [False, (True, {"ok": True, "products": [CARD]})])
    assert stop == "negotiate" and "load_account_retry" in path, path
    assert v["past_due_text"] and v["account_state"] == "ok"


def test_edf07_get_account_keeps_failing_closes_service_error_without_figures():
    w = wf(A)
    v = started(w)
    stop, path = walk(w, v, "load_account", [False, False])
    assert path[-1] == "close_account_error" and v["outcome"] == "no_promise" and v["reason"] == "service_error"
    assert not re.search(r"\d", v["farewell"])


def test_edf08_09_identity_once_retries_twice_closes_service_error():
    w = wf(A)
    idn = w["nodes"]["identity"]
    v = started(w, "2026-10-16T11:00")
    tool_error(v, "verify_identity")
    assert route(idn, v) is None
    tool_error(v, "verify_identity")
    k = route(idn, v)
    assert k == ID_FAIL_CLOSE
    walk(w, v, k, [])
    assert (v["outcome"], v["reason"]) == ("no_promise", "service_error")
    assert "wrong" not in v["farewell"].lower() and "verify" not in v["farewell"].lower()


def test_edf10_callback_fails_once_then_books():
    w = wf(A)
    tp = w["nodes"]["third_party"]
    v = started(w, "2026-11-05T14:00")
    tool_error(v, "schedule_callback")
    assert route(tp, v) is None
    v["schedule_callback_response.status"] = "success"
    v["schedule_callback_response.data.ok"] = True
    v["schedule_callback_success_count"] = 1
    assert route(tp, v) == "close_callback"


def test_edf11_transfer_fails_once_then_transfers():
    w = wf(A)
    v = started(w, "2026-12-03T11:00")
    stop, path = walk(w, v, "transfer_restructuring", [False, True])
    assert path[:2] == ["transfer_restructuring", "transfer_restructuring_retry"]
    assert "close_restructuring" in path and (v["outcome"], v["reason"]) == ("transferred", "restructuring")


def test_edf11_transfer_fails_twice_claims_no_transfer():
    w = wf(A)
    v = started(w, "2026-12-03T11:00")
    stop, path = walk(w, v, "transfer_restructuring", [False, False])
    assert path[-1] == "close_transfer_service_error" and (v["outcome"], v["reason"]) == ("no_promise", "service_error")
    assert "transferring" not in v["farewell"]


def test_transfer_that_works_first_time_is_not_repeated():
    w = wf(A)
    v = started(w)
    stop, path = walk(w, v, "transfer_restructuring", [True])
    assert [p for p in path if "transfer_" in p] == ["transfer_restructuring"], path


def test_add_note_fails_once_is_retried_and_twice_goes_on_to_finish():
    """Brief B: a note is retried once; a second failure keeps the close the wrap chose (a recorded promise stays one)."""
    w = wf(B)
    n = w["nodes"]["note_1"]
    v = started(w)
    v["note_has_2"] = "no"
    api("note_1", n, v, False)
    assert route(n, v) == "note_1_retry"
    api("note_1_retry", w["nodes"]["note_1_retry"], v, False)
    assert route(w["nodes"]["note_1_retry"], v) == "finish"
    v2 = started(w)
    v2["note_has_2"] = "no"
    api("note_1", n, v2, True)
    assert route(n, v2) == "finish"


# ---------------------------------------------------------------- the brief's own rules win

def test_create_promise_keeps_the_briefs_own_retry_path():
    w = wf(A)
    neg = w["nodes"]["negotiate"]
    targets = [t["target_node"] for t in neg["transitions"]]
    assert "retry_promise" in targets and "close_service_error" in targets
    assert w["nodes"]["retry_promise"]["passthrough_default_transition"] == "close_service_error"
    mb = wf(B)
    assert mb["nodes"]["record_1"]["passthrough_default_transition"] == "retry_1"


def test_a_failure_event_that_covers_identity_keeps_its_own_sentence():
    sme = json.load(open("tests/fixtures/bench-sme.spec.json"))
    s = copy.deepcopy(sme)
    for e in s["events"]:
        if e["id"] == "service_error":
            e["stage_say"] = {"identity": "The bank will call you back."}
    w = C(s, json.load(open("bench/sme/openapi.json")), base_url=URL)
    assert "The bank will call you back." in farewell_of(w["nodes"][ID_FAIL_CLOSE])
    # Brief A's failure event is about create_promise: its sentence is not spoken on an identity failure
    a = wf(A)
    fe = [e for e in A["events"] if e["id"] == "service_error"][0]
    for said in filter(None, [fe.get("say")]):
        assert said not in farewell_of(a["nodes"][ID_FAIL_CLOSE])


def test_outcome_names_come_from_the_spec():
    s = copy.deepcopy(A)
    for e in s["events"]:
        if e["id"] == "service_error":
            e["outcome"], e["reason"] = "no_commitment", "system_down"
    for o in s["outcomes"]:
        if o.get("event") == "service_error":
            o["outcome"], o["reason"] = "no_commitment", "system_down"
    w = wf(s)
    for k in ("close_account_error", ID_FAIL_CLOSE, "close_callback_service_error", "close_transfer_service_error"):
        assert close_of(w["nodes"][k]) == ("no_commitment", "system_down"), k


def test_reference_specs_still_lint_clean():
    assert lint(wf(A), A) == []
    assert lint(wf(B), B) == []

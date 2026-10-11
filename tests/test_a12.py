"""Task A12: one product's named days are judged in code (check_date), never by the model.

The hooks run offline as the platform runs them: the pre-hook sees the model's arguments in config['body'] and
writes vars, the post-hook rewrites result['data'] (the tool result the model reads, A9 probe 26dc5ce1)."""
import copy
import json

import pytest

from builder.compile import compile as C
from builder.lint import lint

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
LOAN = {"product_id": "p1", "type": "personal_loan", "past_due": 540.0, "days_late": 5, "instalment_amount": 180.0}


def _code(node):
    return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def _tool(w, name):
    return [t for t in w["nodes"]["negotiate"]["tools"] if t["function"]["name"] == name][0]


def account(now, product=LOAN, spec=A):
    w = C(spec, OA)
    v = {"now": now, "title": "Mr", "last_name": "Ortiz", "acct_first_json": json.dumps(product)}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    assert v["account_state"] == "ok"
    return w, v


def check(w, v, said, date=None):
    t = _tool(w, "check_date")
    body = {"customer_id": "c1", "said": said, "date": date if date is not None else "{{cdx_date}}"}
    exec(t["pre_process_code"], {"vars": v, "config": {"body": body}})
    r = {"status": "success", "header": {"x": 1}, "data": {"products": []}}
    exec(t["post_process_code"], {"vars": v, "result": r})
    return r["data"]


BANK = "https://bank.example/v1"


def promise(w, v, date):
    """(request config after the pre-hook, tool result the model reads). The bank answers as the route it is sent to."""
    t = _tool(w, "create_promise")
    cfg = {"url": BANK + "/create_promise", "method": "POST",
           "body": {"customer_id": "c1", "product_id": "p1", "amount": 540.0, "date": date}}
    exec(t["pre_process_code"], {"vars": v, "config": cfg})
    if cfg["url"].endswith("/get_account"):
        r = {"status": "success", "data": {"customer_id": "c1", "products": [LOAN]}}
    else:
        r = {"status": "success", "data": {"ok": True, "promise_id": "pr1"}}
    exec(t["post_process_code"], {"vars": v, "result": r})
    return cfg, r["data"]


def test_check_date_is_a_read_only_api_tool_the_model_fills():
    w = C(A, OA)
    t = _tool(w, "check_date")
    p = t["function"]["parameters"]["properties"]
    assert t["function"]["handler"] == "__function__:api_call"
    assert p["url"]["default"].endswith("/get_account")
    body = p["body"]["properties"]
    assert body["said"]["default"] == "{{cdx_said}}" and body["date"]["default"] == "{{cdx_date}}"
    assert body["customer_id"]["default"] == "{{customer_id}}"
    # no cdx_* variable is ever set anywhere: the model fills those fields
    assert "'cdx_" not in json.dumps(w).replace('"{{cdx_', "")
    assert lint(w, A) == []


@pytest.mark.parametrize("said", ["Monday the twenty-sixth", "the twenty sixth of October", "Monday the. 26th.",
                                  "Monday the 26th", "twenty-sixth"])
def test_pub07_holiday_in_any_form_offers_the_next_business_day(said):
    w, v = account("2026-10-14T11:00")
    d = check(w, v, said)
    assert d["verdict"] == "not_business_day" and d["date"] == "2026-10-27"
    m = d["what_to_do_next"]
    assert "Monday 26 October 2026 is a bank holiday, not a business day" in m
    assert "offer the next business day, Tuesday 27 October 2026 with the amount of your current attempt" in m
    assert "create_promise date: 2026-10-27" in m
    assert "2 November" not in m
    # the promise then uses the code's date
    cfg, res = promise(w, v, "2026-10-27")
    assert cfg["url"] == BANK + "/create_promise" and cfg["body"]["date"] == "2026-10-27"
    assert res["promise_status"] == "recorded" and res["what_to_do_next"] == "The promise is recorded."


def test_after_deadline_offers_the_deadline_with_the_current_amount():
    w, v = account("2026-10-14T11:00")
    d = check(w, v, "the 6th of November")
    assert d["verdict"] == "after_deadline" and d["date"] == "2026-11-02"
    assert ("Say the latest date you can accept is Monday 2 November 2026 and offer it with the amount of your current attempt"
            in d["what_to_do_next"])
    assert "Their own date is never a refusal." in d["what_to_do_next"]
    s = copy.deepcopy(A)
    s["negotiation"]["after_deadline"] = "reject"
    w, v = account("2026-10-14T11:00", spec=s)
    d = check(w, v, "the 6th of November")
    assert d["date"] == "" and "cannot be accepted" in d["what_to_do_next"] and "current attempt" not in d["what_to_do_next"]


def test_ok_past_vague_and_iso_fallback():
    w, v = account("2026-10-14T11:00")
    d = check(w, v, "Friday the 23rd")
    assert d["verdict"] == "ok" and d["date"] == "2026-10-23" and "Friday 23 October 2026 can be accepted" in d["what_to_do_next"]
    d = check(w, v, "the 13th of October")
    assert d["verdict"] == "past" and d["date"] == "" and "already passed" in d["what_to_do_next"]
    d = check(w, v, "the week after next", "2026-10-26")       # a guessed ISO date never turns a period into a day
    assert d["verdict"] == "vague" and "vague period" in d["what_to_do_next"]
    d = check(w, v, "that day", "2026-10-26")                  # no day in the words: the model's ISO date is judged
    assert d["verdict"] == "not_business_day" and d["date"] == "2026-10-27"
    d = check(w, v, "hmm")
    assert d["verdict"] == "no_day" and "ask for the exact day" in d["what_to_do_next"]


def test_late_band_and_other_now_values():
    late = dict(LOAN, days_late=20)
    w, v = account("2026-10-23T11:00", late)                   # Friday before the holiday Monday; deadline Wed 4 Nov
    assert check(w, v, "next Monday")["date"] == "2026-10-27"
    assert check(w, v, "tomorrow")["date"] == "2026-10-27"
    w, v = account("2026-12-23T11:00")                         # holiday cluster; early deadline Tue 12 Jan 2027
    d = check(w, v, "the first of January")
    assert d["verdict"] == "not_business_day" and d["date"] == "2027-01-04"
    w, v = account("2026-10-30T11:00")                         # month end
    assert check(w, v, "the 31st")["date"] == "2026-11-02"


def test_fix1_promise_with_a_date_the_rules_do_not_allow_is_never_recorded():
    """Fix round 1, item 2: no silent correction. The call goes to the read-only route and the result says NOT RECORDED."""
    w, v = account("2026-10-14T11:00")
    cfg, res = promise(w, v, "2026-10-26")                     # the model skipped check_date on a holiday
    assert cfg["url"] == BANK + "/get_account" and cfg["body"] == {"customer_id": "c1"}
    assert res["ok"] is False and res["promise_status"] == "rejected"
    m = res["what_to_do_next"]
    assert m.startswith("NOT RECORDED: Monday 26 October 2026 is a bank holiday, not a business day; offer the next business day, "
                        "Tuesday 27 October 2026 (create_promise date 2026-10-27)")
    assert m.endswith("Say this date, read it back and ask again.")
    assert v["promise_date_used"] == "" and "promise_fail_n" not in v
    cfg, res = promise(w, v, "2026-11-20")
    assert cfg["url"].endswith("/get_account") and "the latest date you can accept is Monday 2 November 2026" in res["what_to_do_next"]
    cfg, res = promise(w, v, "2026-10-12")
    assert cfg["url"].endswith("/get_account") and "has already passed" in res["what_to_do_next"]
    # no usable date: rejected, and an earlier check_date value is never picked up
    check(w, v, "Monday the twenty-sixth")
    assert v["cd_date"] == "2026-10-27"
    cfg, res = promise(w, v, "{{promise_date}}")
    assert cfg["url"].endswith("/get_account") and "date" not in cfg["body"]
    assert res["what_to_do_next"].startswith("NOT RECORDED: the date was not given as YYYY-MM-DD")
    # an acceptable date goes to the bank unchanged
    cfg, res = promise(w, v, "2026-10-16")
    assert cfg["url"] == BANK + "/create_promise" and cfg["body"]["date"] == "2026-10-16"
    assert res["what_to_do_next"] == "The promise is recorded." and v["promise_date_used"] == "2026-10-16"


def test_fix1_rejected_promise_is_no_service_failure_in_the_graph():
    """The rejected result fires neither the recorded close nor the retry / failure routes."""
    w, v = account("2026-10-14T11:00")
    _, res = promise(w, v, "2026-10-26")
    neg = w["nodes"]["negotiate"]
    rec = [t for t in neg["transitions"] if t.get("target_node") == "close_promise_recorded"][0]
    conds = json.dumps(rec["condition"])
    assert '"promise_status"' in conds and '"recorded"' in conds and res["promise_status"] == "rejected" and res["ok"] is False
    # retry needs an HTTP error status; the failure close needs promise_fail_n, which a rejection never counts
    assert "promise_fail_n" not in v


NOWS = ("2026-10-16T11:00", "2026-10-23T11:00", "2026-10-14T19:30", "2026-10-30T11:00", "2026-12-23T11:00")


@pytest.mark.parametrize("now", NOWS)
def test_fix1_hook_takes_the_corrected_day_and_asks_on_week_phrases(now):
    w, v = account(now)
    a = check(w, v, "the 20th, no, the 23rd")
    b = check(w, v, "the 23rd")
    assert (a["verdict"], a["date"]) == (b["verdict"], b["date"])
    a = check(w, v, "not Friday, Thursday")
    b = check(w, v, "Thursday")
    assert (a["verdict"], a["date"]) == (b["verdict"], b["date"])
    for said in ("a week on Friday", "Friday week", "the second week"):
        d = check(w, v, said, "2026-10-30")                    # a guessed ISO date is ignored for a period
        assert d["verdict"] == "vague" and d["date"] == "", (now, said)


def test_fix1_after_hours_today():
    # brief A has no payment cut-off (rule 19: today or later), so today stays possible at 19:30
    w, v = account("2026-10-14T19:30")
    d = check(w, v, "Wednesday")
    assert d["verdict"] == "ok" and d["date"] == "2026-10-14"
    # a spec that names a same-day cut-off: today after it is offered the next business day, and never recorded
    s = copy.deepcopy(A)
    s["negotiation"]["same_day_cutoff"] = "18:00"
    w, v = account("2026-10-14T19:30", spec=s)
    for said in ("Wednesday", "today"):
        d = check(w, v, said)
        assert d["verdict"] == "today_closed" and d["date"] == "2026-10-15", said
        assert "TOO LATE FOR TODAY: it is after 18:00" in d["what_to_do_next"]
        assert "offer the next business day, Thursday 15 October 2026" in d["what_to_do_next"]
    cfg, res = promise(w, v, "2026-10-14")
    assert cfg["url"].endswith("/get_account") and res["what_to_do_next"].startswith("NOT RECORDED: it is after 18:00")
    w, v = account("2026-10-14T11:00", spec=s)
    assert check(w, v, "today")["verdict"] == "ok"
    from builder.validate import validate
    assert validate(s, OA) == []                               # the optional field is in the spec schema
    s["negotiation"]["same_day_cutoff"] = "7pm"
    assert validate(s, OA) != []


def test_retry_uses_the_date_sent_to_the_bank():
    w, v = account("2026-10-14T11:00")
    promise(w, v, "2026-10-23")
    v["create_promise_args.promise_amount"] = 540
    v["create_promise_args.promise_date"] = "2026-10-23"
    t = w["nodes"]["retry_promise"]["pre_actions"][0]
    body = {"customer_id": "c1", "product_id": "p1", "amount": 1, "date": "x"}
    exec(t["pre_process_code"], {"vars": v, "config": {"body": body}})
    assert body["date"] == "2026-10-23" and body["amount"] == 540.0


def test_hooks_survive_bad_input():
    w, v = account("2026-10-14T11:00")
    t = _tool(w, "check_date")
    for cfg in ({}, {"body": None}, {"body": {"said": None}}, None):
        exec(t["pre_process_code"], {"vars": v, "config": cfg})
        assert v["cd_kind"] in ("no_day", "error")
    vv = {}
    exec(t["pre_process_code"], {"vars": vv, "config": {"body": {"said": "the 26th"}}})   # no deadline_iso
    assert vv["cd_kind"] == "error" and "check_date again" in vv["cd_msg"]
    for r in ({}, None, "x", {"data": None}):
        exec(t["post_process_code"], {"vars": vv, "result": r})


def test_prompt_no_longer_carries_the_calendar_and_asks_for_check_date():
    t = C(A, OA)["nodes"]["negotiate"]["task_message"]
    for gone in ("{{calendar_text}}", "{{allowed_dates_text}}", "{{weekday_text}}", "Dates you may accept"):
        assert gone not in t
    assert "call check_date with their latest words for it (a corrected day replaces the earlier one" in t
    assert "Never judge a date yourself" in t and '"twenty-sixth"' in t
    # PUB-04/06/07 behaviours kept
    assert "A date with no amount: the amount is the one of your current attempt" in t
    assert "ask once for the exact day" in t
    assert 'and add "The rest stays overdue." once' in t and "offer the floor {{floor_text}} once" in t
    assert len(t) <= 4800


def test_multi_product_is_unchanged():
    w = C(B, OA)
    assert [x["function"]["name"] for x in w["nodes"]["negotiate"]["tools"]] == ["set_product"]
    assert "{{calendar_text}}" in w["nodes"]["negotiate"]["task_message"]
    assert lint(w, B) == []

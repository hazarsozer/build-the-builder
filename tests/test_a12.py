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


def promise(w, v, date):
    t = _tool(w, "create_promise")
    body = {"customer_id": "c1", "product_id": "p1", "amount": 540.0, "date": date}
    exec(t["pre_process_code"], {"vars": v, "config": {"body": body}})
    r = {"status": "success", "data": {"ok": True}}
    exec(t["post_process_code"], {"vars": v, "result": r})
    return body, r["data"]


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
    assert "offer the next business day, Tuesday 27 October 2026, with the amount of your current attempt" in m
    assert "create_promise date: 2026-10-27" in m
    assert "2 November" not in m
    # the promise then uses the code's date
    body, res = promise(w, v, "2026-10-27")
    assert body["date"] == "2026-10-27" and res["what_to_do_next"] == "The promise is recorded."


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


def test_promise_date_is_corrected_to_what_the_rules_allow():
    w, v = account("2026-10-14T11:00")
    body, res = promise(w, v, "2026-10-26")                    # the model skipped check_date on a holiday
    assert body["date"] == "2026-10-27" and v["promise_date_used"] == "2026-10-27"
    assert "Tuesday 27 October 2026" in res["what_to_do_next"]
    body, _ = promise(w, v, "2026-11-20")
    assert body["date"] == "2026-11-02"
    check(w, v, "Monday the twenty-sixth")
    body, _ = promise(w, v, "{{promise_date}}")                 # no usable date: check_date's date
    assert body["date"] == "2026-10-27"
    body, res = promise(w, v, "2026-10-16")                     # an acceptable date is never changed
    assert body["date"] == "2026-10-16" and res["what_to_do_next"] == "The promise is recorded."


def test_retry_uses_the_corrected_date():
    w, v = account("2026-10-14T11:00")
    promise(w, v, "2026-10-26")
    v["create_promise_args.promise_amount"] = 540
    v["create_promise_args.promise_date"] = "2026-10-26"
    t = w["nodes"]["retry_promise"]["pre_actions"][0]
    body = {"customer_id": "c1", "product_id": "p1", "amount": 1, "date": "x"}
    exec(t["pre_process_code"], {"vars": v, "config": {"body": body}})
    assert body["date"] == "2026-10-27"


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
    assert "call check_date with their words for it, joined across turns" in t
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

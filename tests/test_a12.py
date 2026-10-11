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


def check(w, v, said, date=None, attempt=None, floor=None):
    t = _tool(w, "check_date")
    body = {"customer_id": "c1", "said": said, "date": date if date is not None else "{{cdx_date}}",
            "attempt": attempt if attempt is not None else "{{cdx_attempt}}",
            "floor_offered": floor if floor is not None else "{{cdx_floor_offered}}"}
    exec(t["pre_process_code"], {"vars": v, "config": {"body": body}})
    r = {"status": "success", "header": {"x": 1}, "data": {"products": []}}
    exec(t["post_process_code"], {"vars": v, "result": r})
    return r["data"]


BANK = OA["servers"][0]["url"].rstrip("/")


def promise(w, v, date):
    """(request config after the pre-hook, tool result the model reads). The bank answers as the route it is sent to."""
    t = _tool(w, "create_promise")
    cfg = {"url": t["function"]["parameters"]["properties"]["url"]["default"], "method": "POST",
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
    assert "offer the next business day, Tuesday 27 October 2026, as a yes/no question, for the amount of your current attempt" in m
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
    assert ("Say the latest date you can accept is Monday 2 November 2026 and offer it, as a yes/no question, for the amount of your current attempt"
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


# ---------------------------------------------------------------- fix round 2: the guard fails closed

NOT_CHECKED = "NOT RECORDED: the date could not be checked; ask the customer for the day again."


def _pre(t, v, cfg):
    exec(t["pre_process_code"], {"vars": v, "config": cfg})
    return cfg


def _no_unchecked_promise(cfg):
    assert not str(cfg.get("url") or "").endswith("/create_promise"), cfg
    assert cfg["url"] == BANK + "/get_account" and set(cfg["body"]) == {"customer_id"}, cfg


def test_fix2_promise_tools_default_to_the_read_only_route():
    """If a pre-hook never runs, the request goes to get_account: nothing can reach create_promise unchecked."""
    w = C(A, OA)
    for node in ("negotiate", "retry_promise"):
        ts = w["nodes"][node].get("tools", []) + w["nodes"][node].get("pre_actions", [])
        t = [x for x in ts if x["function"]["name"] == "create_promise"][0]
        assert t["function"]["parameters"]["properties"]["url"]["default"] == BANK + "/get_account", node
    # and the result of such a call is never "recorded"
    t = _tool(w, "create_promise")
    r = {"status": "success", "data": {"customer_id": "c1", "products": [LOAN]}}
    exec(t["post_process_code"], {"vars": {}, "result": r})
    assert r["data"]["promise_status"] == "rejected" and r["data"]["what_to_do_next"] == NOT_CHECKED


@pytest.mark.parametrize("dl", [None, "", "garbage", "2026-13-45", 42])
def test_fix2_unreadable_deadline_keeps_a_holiday_off_the_bank(dl):
    w, v = account("2026-10-14T11:00")
    if dl is None:
        v.pop("deadline_iso")
    else:
        v["deadline_iso"] = dl
    t = _tool(w, "create_promise")
    for date in ("2026-10-26", "2026-10-23"):                 # even an acceptable date: it could not be checked
        cfg = _pre(t, v, {"url": BANK + "/get_account", "body": {"customer_id": "c1", "product_id": "p1", "amount": 540, "date": date}})
        _no_unchecked_promise(cfg)
        assert v["promise_reject"] == NOT_CHECKED and v["promise_date_used"] == ""
        r = {"status": "success", "data": {"products": []}}
        exec(t["post_process_code"], {"vars": v, "result": r})
        assert r["data"]["promise_status"] == "rejected" and r["data"]["what_to_do_next"] == NOT_CHECKED


@pytest.mark.parametrize("body", [None, "date=2026-10-26", ["2026-10-26"], 7])
def test_fix2_non_dict_body_is_rejected_and_rerouted(body):
    w, v = account("2026-10-14T11:00")
    cfg = _pre(_tool(w, "create_promise"), v, {"url": BANK + "/create_promise", "body": body})
    _no_unchecked_promise(cfg)
    assert v["promise_reject"] == NOT_CHECKED


def test_fix2_config_without_url_is_still_rerouted():
    w, v = account("2026-10-14T11:00")
    t = _tool(w, "create_promise")
    cfg = _pre(t, v, {"body": {"customer_id": "c1", "amount": 540, "date": "2026-10-26"}})
    _no_unchecked_promise(cfg)
    cfg = _pre(t, v, {"body": {"customer_id": "c1", "amount": 540, "date": "2026-10-23"}})
    assert cfg["url"] == BANK + "/create_promise" and cfg["body"]["date"] == "2026-10-23"


def test_fix2_retry_reuses_only_a_date_the_guard_accepted():
    w, v = account("2026-10-14T11:00")
    rt = w["nodes"]["retry_promise"]["pre_actions"][0]

    def retry(vv):
        return _pre(rt, vv, {"url": BANK + "/get_account", "body": {"customer_id": "c1", "product_id": "p1", "amount": 1,
                                                                    "date": "2026-10-26"}})
    # never sent / rejected / guard crashed: the model's raw date is never used
    _no_unchecked_promise(retry({"create_promise_args.promise_amount": 540, "create_promise_args.promise_date": "2026-10-26"}))
    promise(w, v, "2026-10-26")                                # rejected by the guard
    v.update({"create_promise_args.promise_amount": 540, "create_promise_args.promise_date": "2026-10-26"})
    _no_unchecked_promise(retry(v))
    _no_unchecked_promise(retry({"promise_date_used": "2026-10-23"}))   # a date without the guard's "sent" mark
    # accepted by the guard: the retry sends exactly that date
    cfg, _ = promise(w, v, "2026-10-23")
    v["create_promise_args.promise_date"] = "2026-10-23"
    cfg = retry(v)
    assert cfg["url"] == BANK + "/create_promise" and cfg["body"]["date"] == "2026-10-23" and cfg["body"]["amount"] == 540.0
    # the retry node's success route needs data.ok from create_promise; get_account's answer has none
    tr = json.dumps(w["nodes"]["retry_promise"]["transitions"])
    assert "retry_ok" in tr


# ---------------------------------------------------------------- fix round 3: an accepted offer is confirmed, not re-offered

def test_fix3_pub06_accepting_the_offered_date_is_confirmed_not_offered_again():
    """PUB-06 (regate-a12-A): "I can do that on Friday" after the offer of Friday 16 Oct must not bring a second offer."""
    w, v = account("2026-10-14T11:00")
    assert v["offer_dates"] == "2026-10-16" and v["offer_amounts"] == "current"
    for said in ("I can do that on Friday.", "Friday", "the 16th"):
        d = check(w, v, said, attempt="1")
        m = d["what_to_do_next"]
        assert d["verdict"] == "ok" and d["date"] == "2026-10-16", said
        assert m.startswith("ACCEPTED OFFER: Friday 16 October 2026 is a date you proposed")
        assert "Do not propose it again." in m and "accepted your offer" not in m
        assert "As a confirmation, read back once the product and 540.00 on Friday 16 October 2026" in m
        assert m.endswith('ask "Do you confirm?", then record (create_promise date: 2026-10-16).')
        assert "propose this date" not in m and "current attempt" not in m


def test_fix3_dates_from_a_result_count_as_offers():
    w, v = account("2026-10-14T11:00")
    check(w, v, "Monday the twenty-sixth", attempt="1")        # result: offer Tuesday 27 October
    assert v["cd_offered"] == "2026-10-27"
    assert check(w, v, "Tuesday is fine", attempt="1")["what_to_do_next"].startswith("ACCEPTED OFFER: Tuesday 27 October 2026")
    check(w, v, "the 20th of November", attempt="1")            # result: offer the deadline
    assert check(w, v, "the second of November", attempt="1")["what_to_do_next"].startswith("ACCEPTED OFFER: Monday 2 November 2026")


def test_fix3_a_new_date_named_by_the_customer():
    w, v = account("2026-10-14T11:00")
    m = check(w, v, "the 23rd", attempt="1")["what_to_do_next"]
    assert m.startswith("NEW DATE: Friday 23 October 2026 can be accepted. You have not offered it: do not offer it now. ")
    assert "read back once the product and 540.00 on Friday 23 October 2026" in m
    assert "This is not a question about paying and not a new offer." in m


@pytest.mark.parametrize("now", NOWS)
def test_fix3_no_verdict_restates_an_amount_already_heard(now):
    w, v = account(now)
    for said in ("Friday", "the 23rd", "Monday the twenty-sixth", "the 20th of November", "tomorrow", "the 31st", "today"):
        m = check(w, v, said)["what_to_do_next"]
        assert "with the amount" not in m and "Can you pay" not in m and "propose this date" not in m, (now, said, m)
        if "current attempt" in m:
            assert "never say a figure the customer already heard" in m, (now, said, m)


def test_fix3_prompt_checks_only_days_the_customer_names():
    t = C(A, OA)["nodes"]["negotiate"]["task_message"]
    assert "A day the customer names themselves" in t
    assert "A yes to a date you proposed needs no check: go to the read-back." in t
    assert len(t) <= 4800


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


# ---- fix round 4: ACCEPTED OFFER only for a date the agent has really proposed; otherwise a NEW DATE read-back
LATE = dict(LOAN, days_late=20)


def _plan(now, spec=A):
    w, v = account(now, LATE, spec)
    pd, pk = v["offer_dates"].split(","), v["offer_amounts"].split(",")
    assert pk == ["current", "floor"] and pd[1] == v["deadline_iso"]
    return w, v, pd


def _iso_said(iso):
    import datetime
    d = datetime.date.fromisoformat(iso)
    return "the %d of %s" % (d.day, ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                                      "October", "November", "December"][d.month - 1])


@pytest.mark.parametrize("now", NOWS)
def test_fix4_plan_dates_count_only_once_the_attempt_was_said(now):
    """The deadline (attempt 2 of the late band) and attempt 1's date, named by the customer before the agent offered
    them, are NEW DATEs with the current attempt's amount; once the attempt was said, ACCEPTED OFFER with its amount."""
    w, v, pd = _plan(now)
    for k, iso in enumerate(pd):
        for att in ([None, "", "0", "{{cdx_attempt}}", "x"] + (["1"] if k == 1 else [])):
            d = check(w, v, _iso_said(iso), attempt=att)
            m = d["what_to_do_next"]
            if d["verdict"] != "ok":
                continue                                       # e.g. a plan date that is today after hours never occurs here
            assert d["date"] == iso, (now, iso, att)
            assert m.startswith("NEW DATE: "), (now, iso, att, m)
            assert "accepted your offer" not in m and "Can you pay" not in m and "propose this date" not in m
            # amount of the current attempt: attempt 1 (current) before attempt 2 was said
            assert "read back once the product and 540.00 on " in m, (now, att, m)
        v.pop("cd_offered", None)
    # attempt 1 said: its date is accepted with attempt 1's amount
    m = check(w, v, _iso_said(pd[0]), attempt="1")["what_to_do_next"]
    assert m.startswith("ACCEPTED OFFER: "), (now, m)
    assert "read back once the product and 540.00 on " in m
    # attempt 2 said: the deadline is accepted with the floor; a figure-free offer is never asked for
    m = check(w, v, _iso_said(pd[1]), attempt="2")["what_to_do_next"]
    assert m.startswith("ACCEPTED OFFER: "), (now, m)
    assert "read back once the product and 180.00 on " in m and "540.00" not in m
    # a new date after attempt 2 reads back attempt 2's amount (the floor)
    m = check(w, v, "today", attempt="2")["what_to_do_next"]
    if m.startswith("NEW DATE"):
        assert "read back once the product and 180.00 on " in m and "540.00" not in m


@pytest.mark.parametrize("now", NOWS)
def test_fix4_attempt_one_date_named_first_is_a_new_date(now):
    """The issue: on attempt 1 (full amount, early band) the customer names the plan date or the deadline before
    the agent said it: never "The customer accepted your offer"; the read-back names the current attempt's amount."""
    w, v = account(now)
    for iso in (v["offer_dates"].split(",")[0], v["deadline_iso"]):
        d = check(w, v, _iso_said(iso), attempt="0")
        if d["verdict"] != "ok":
            continue
        m = d["what_to_do_next"]
        assert m.startswith("NEW DATE: ") and "accepted your offer" not in m, (now, iso, m)
        assert "540.00" in m and m.count(iso) == 1 and m.endswith("record (create_promise date: " + iso + ").")
    # the deadline is never an offer by itself, even after attempt 1 was said (the early band has one attempt)
    if v["deadline_iso"] not in v["offer_dates"]:
        assert check(w, v, _iso_said(v["deadline_iso"]), attempt="1")["what_to_do_next"].startswith("NEW DATE")


def test_fix4_attempt_out_of_range_is_clamped():
    w, v, pd = _plan("2026-10-14T11:00")
    assert pd[0] != pd[1]
    assert check(w, v, _iso_said(pd[1]), attempt="7")["what_to_do_next"].startswith("ACCEPTED OFFER")
    for att in ("-1", "two", "1", " 1 "):                      # unreadable or too low: the deadline is not yet offered
        assert check(w, v, _iso_said(pd[1]), attempt=att)["what_to_do_next"].startswith("NEW DATE"), att


def test_fix4_check_date_asks_for_the_attempt_and_date_only_reads_back():
    w = C(A, OA)
    body = _tool(w, "check_date")["function"]["parameters"]["properties"]["body"]
    assert body["properties"]["attempt"]["default"] == "{{cdx_attempt}}"
    assert body["properties"]["floor_offered"]["default"] == "{{cdx_floor_offered}}"
    assert body["properties"]["floor_offered"]["description"] == "yes if you have already offered {{floor_text}} in an earlier message"
    assert "the last plan attempt you already said in an earlier message" in body["properties"]["attempt"]["description"]
    for f in ("attempt", "floor_offered"):
        assert f in body.get("required", [f])
    pre = _tool(w, "check_date")["pre_process_code"]
    assert "{{" not in pre and "}}" not in pre
    t = w["nodes"]["negotiate"]["task_message"]
    assert "Never offer it again: read it back with their date once, as the confirmation of step 4" in t
    assert "Propose that amount with the date" not in t
    assert len(t) <= 4800
    assert lint(w, A) == []


# ---- fix round 5: one figure per verdict; a "current" amount is the floor once the model says it offered it
PHRASES = ("Friday", "the 23rd", "Monday the twenty-sixth", "the 20th of November", "tomorrow", "the 31st", "today")


@pytest.mark.parametrize("now", NOWS)
@pytest.mark.parametrize("product", [LOAN, LATE])
def test_fix5_no_verdict_contains_two_amounts(now, product):
    w, v = account(now, product)
    plan = v["offer_dates"].split(",")
    for att in ("0", "1", "2", "{{cdx_attempt}}"):
        for fo in ("yes", "no", "maybe", "{{cdx_floor_offered}}"):
            v.pop("cd_offered", None)
            for said in PHRASES + tuple(_iso_said(x) for x in plan):
                m = check(w, v, said, attempt=att, floor=fo)["what_to_do_next"]
                assert not ("540.00" in m and "180.00" in m), (now, att, fo, said, m)


@pytest.mark.parametrize("now", NOWS)
def test_fix5_floor_offered_picks_one_figure(now):
    w, v = account(now)
    iso = v["offer_dates"]
    for fo, fig in (("yes", "180.00"), ("Yes.", "180.00"), ("no", "540.00"), ("garbage", "540.00"), ("", "540.00"),
                    ("{{cdx_floor_offered}}", "540.00")):
        for att in ("0", "1"):
            d = check(w, v, _iso_said(iso), attempt=att, floor=fo)
            if d["verdict"] != "ok":
                continue
            m = d["what_to_do_next"]
            assert "read back once the product and " + fig + " on " in m, (now, fo, att, m)
    # a floor attempt names the floor whatever floor_offered says
    w, v, pd = _plan(now)
    for fo in ("yes", "no", "garbage"):
        d = check(w, v, _iso_said(pd[1]), attempt="2", floor=fo)
        if d["verdict"] == "ok":
            assert "read back once the product and 180.00 on " in d["what_to_do_next"] and "540.00" not in d["what_to_do_next"]


def test_fix5_no_cdx_variable_is_set_in_code():
    w = C(A, OA)
    assert "'cdx_" not in json.dumps(w).replace('"{{cdx_', "")
    pre = _tool(w, "check_date")["pre_process_code"]
    assert "accepted your offer" not in pre and "{{" not in pre and "}}" not in pre

"""Task 7b: the builder handles briefs it has never seen (renamed fields, extra tool fields, aliases)."""
import copy
import json

from builder import cases, oracle
from builder.compile import compile as C
from builder.lib import dates
from builder.lint import lint
from builder.validate import validate

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))


def _code(node):
    return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def _tools(w):
    for k, n in w["nodes"].items():
        for t in n.get("tools", []) + n.get("pre_actions", []):
            yield k, t
    for t in w.get("tools", []):
        yield "workflow", t


def run(code, result=None, vars_=None, config=None):
    g = {"result": result, "vars": vars_ if vars_ is not None else {}, "config": config if config is not None else {}}
    exec(code, g)
    return g


# ---------------------------------------------------------------- G1 product field mapping

AMT, DAYS = "premium_due", "days_overdue"


def _renamed_oa():
    oa = copy.deepcopy(OA)
    items = oa["paths"]["/get_account"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["products"]["items"]
    props = items["properties"]
    props[AMT] = props.pop("past_due")
    props[DAYS] = props.pop("days_late")
    return oa


def _renamed(spec):
    s = copy.deepcopy(spec)
    for p in s["products"].values():
        p["amount_field"] = AMT
        p["days_late_field"] = DAYS
    return s


def _ren(p):
    p = dict(p)
    p[AMT] = p.pop("past_due")
    p[DAYS] = p.pop("days_late")
    return p


CARD = {"product_id": "p1", "type": "credit_card", "card_last4": "4821", "past_due": 420.0, "days_late": 20,
        "minimum_payment": 35.0}


def test_g1_reference_specs_valid_without_mapping():
    for s in (A, B):
        assert validate(s, OA) == []


def test_g1_validate_accepts_mapped_fields_and_rejects_unknown():
    oa = _renamed_oa()
    assert validate(_renamed(A), oa) == []
    bad = _renamed(A)
    bad["products"]["car_loan"]["amount_field"] = "nope"
    errs = validate(bad, oa)
    assert any("amount_field" in e and "nope" in e for e in errs), errs
    # without the mapping, a renamed openapi has no past_due field
    assert any("past_due" in e for e in validate(A, oa)), validate(A, oa)


def _account(spec, product, now="2026-10-14T11:00"):
    w = C(spec, _renamed_oa())
    v = {"now": now, "title": "Mr", "last_name": "Reyes", "acct_products_json": json.dumps([product])}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    return v


def test_g1_prepare_account_reads_mapped_fields():
    v = _account(_renamed(A), _ren(CARD))
    assert v["account_state"] == "ok", v.get("account_error")
    assert v["past_due_text"] == "420.00" and v["days_late_text"] == "20"
    assert "2026-10-23" in v["deadline_text"]                     # late band: 7th business day
    assert "420.00" in v["debt_statement_text"] and "20 days late" in v["debt_statement_text"]


def test_g1_multi_reads_mapped_fields():
    s = _renamed(B)
    prods = [_ren(CARD), _ren(dict(CARD, product_id="p2", type="car_loan", past_due=900.0, days_late=3,
                                    instalment_amount=210.0))]
    w = C(s, _renamed_oa())
    v = {"now": "2026-10-14T11:00", "title": "Mr", "last_name": "Reyes", "acct_products_json": json.dumps(prods)}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    assert v["account_state"] == "ok", v.get("account_error")
    st = json.loads(v["mp_state"])
    assert st["p"]["p1"]["full"] == 420.0 and st["p"]["p2"]["full"] == 900.0
    assert st["p"]["p1"]["dl"] == "2026-10-23" and st["p"]["p2"]["dl"] == "2026-11-02"


def test_g1_multi_order_uses_mapped_fields():
    s = _renamed(B)
    s["multi_product"]["order"] = "highest_past_due"
    prods = [_ren(CARD), _ren(dict(CARD, product_id="p2", type="car_loan", past_due=900.0, days_late=3,
                                    instalment_amount=210.0))]
    w = C(s, _renamed_oa())
    v = {"now": "2026-10-14T11:00", "acct_products_json": json.dumps(prods)}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    assert json.loads(v["mp_state"])["order"] == ["p2", "p1"]


def test_g1_plan_products_uses_mapping():
    s = _renamed(B)
    plans = dates.plan_products([_ren(CARD)], dates.datetime.date(2026, 10, 14), s["bands"], s["calendar"], s["products"])
    assert plans[0]["full"] == 420.0 and plans[0]["deadline"].isoformat() == "2026-10-23"


def test_g1_oracle_uses_mapped_fields():
    s = _renamed(A)
    scen = [{"id": "x", "now": "2026-10-14T11:00", "products": [_ren(CARD)], "customer_says": "", "quote": "",
             "expected": {"outcome": "promise", "reason": None,
                          "deadlines": {"p1": "2026-10-23"},
                          "offers": [{"product_id": "p1", "attempt": 1, "amount": 420.0, "date": "2026-10-16"},
                                     {"product_id": "p1", "attempt": 2, "amount": 35.0, "date": "2026-10-23"}],
                          "promises": [{"product_id": "p1", "amount": 35.0, "date": "2026-10-23"}]}}]
    assert oracle.check(s, scen) == []
    s2 = _renamed(B)
    s2["multi_product"]["order"] = "highest_past_due"
    prods = [_ren(CARD), _ren(dict(CARD, product_id="p2", past_due=900.0))]
    assert oracle._order(s2, prods) == ["p2", "p1"]


def test_g1_cases_seed_mapped_fields():
    s = _renamed(A)
    oa = _renamed_oa()
    got = cases.generate(s, oa, "2026-10-14T11:00", n=10, seed=0)
    assert len(got) >= 5
    for c in got:
        for p in c["seed"]["products"]:
            assert AMT in p and DAYS in p and "past_due" not in p and "days_late" not in p, p
        for pr in c["expected"]["promises"]:
            prod = c["seed"]["products"][int(pr["product_id"][1:]) - 1]
            assert pr["amount"] <= prod[AMT] + 0.005
    happy = next(c for c in got if c["scenario"] == "happy_full_today")
    assert happy["expected"]["promises"][0]["amount"] == happy["seed"]["products"][0][AMT]


def test_g1_cases_multi_mapped():
    s = _renamed(B)
    c = cases.build(s, _renamed_oa(), "multi_product", "2026-10-14T11:00")
    assert c is not None
    assert all(AMT in p for p in c["seed"]["products"])


def test_g7_year_format_in_schema_and_compiled():
    s = copy.deepcopy(A)
    s["identity"]["questions"][1] = {"field": "phone_last4", "ask": "What year were you born?", "format": "year"}
    assert validate(s, OA) == []
    w = C(s, OA)
    pre = [t for k, t in _tools(w) if t["function"]["name"] == "verify_identity"][0]["pre_process_code"]
    for ans, part, want in [("88", "19", "1988"), ("19", "88", None), ("2150", "", None), ("2026", "", "2026")]:
        cfg = {"body": {"field": "phone_last4", "answer": ans}}
        run(pre, None, {"now": "2026-10-14T11:00", "id_partial": part, "id_partial_field": "phone_last4"}, cfg)
        assert cfg["body"]["answer"] == (want or ""), (ans, part, cfg)


def test_g1_compile_lint_clean_with_mapping():
    for spec in (_renamed(A), _renamed(B)):
        w = C(spec, _renamed_oa())
        assert [p for p in lint(w, spec) if not p.startswith("warning:")] == []


# ---------------------------------------------------------------- G2 extra call variables

def _company(spec=None):
    s = copy.deepcopy(spec or A)
    s["call_variables"].append("company_name")
    s["opening"]["confirm_question"] = "Am I speaking with {title} {last_name} of {company_name}?"
    s["address_as"] = "{title} {last_name} of {company_name}"
    return s


def test_g2_extra_call_variable_in_prompts_code_and_test_variables():
    s = _company()
    assert validate(s, OA) == []
    w = C(s, OA)
    assert "{{company_name}}" in w["nodes"]["opening"]["on_enter"]["text"]
    assert "{{company_name}}" in w["system_prompt"]
    assert "company_name" in w["test_variables"] and w["test_variables"]["customer_id"] == "c_test"
    for k in ("first_name", "last_name", "title", "now"):
        assert k not in w["test_variables"]
    assert [p for p in lint(w, s) if not p.startswith("warning:")] == []
    # code nodes read it from vars: a farewell that names the customer
    v = {"title": "Ms", "last_name": "Okafor", "company_name": "Okafor Joinery"}
    exec(_code(w["nodes"]["close_promise_recorded"]), {"vars": v})
    assert "Okafor Joinery" in v["farewell"] and "{" not in v["farewell"]


def test_g2_reference_test_variables_unchanged():
    assert C(A, OA)["test_variables"] == {"customer_id": "c_test"}


def test_g2_cases_seed_extra_variables():
    s = _company()
    for c in cases.generate(s, OA, "2026-10-14T11:00", n=4, seed=1):
        val = c["seed"]["variables"].get("company_name")
        assert val and val != "x", c["seed"]["variables"]
        assert c["variables"]["company_name"] == val


# ---------------------------------------------------------------- G6 sentence after verification

PVS = "This is an attempt to collect a debt and any information obtained will be used for that purpose."


def test_g6_post_verify_sentence_spoken_before_the_debt():
    for spec in (A, B):
        s = copy.deepcopy(spec)
        s["negotiation"]["post_verify_sentence"] = PVS
        assert validate(s, OA) == []
        n = C(s, OA)["nodes"]["negotiate"]
        text = n["on_enter"]["text"]
        assert text.index("verified") < text.index(PVS) < text.index("{{debt_statement_text}}"), text
        assert PVS in n["task_message"]
        assert PVS in cases.fixed_sentences(s)
    assert len(C(dict(copy.deepcopy(A), negotiation=dict(A["negotiation"], post_verify_sentence=PVS)), OA)
               ["nodes"]["negotiate"]["task_message"]) <= 4000 + len(PVS) + 1


def test_g6_reference_unchanged():
    assert C(A, OA)["nodes"]["negotiate"]["on_enter"]["text"].startswith("Thank you, you are verified. {{debt_statement_text}}")


# ---------------------------------------------------------------- G8 bank generalisation

from bank import server  # noqa: E402

SEED = {"variables": {"first_name": "A", "last_name": "B", "title": "Mr", "now": "2026-10-14T11:00"},
        "identity": {"date_of_birth": "1988-03-14", "phone_last4": "4471"},
        "products": [{"type": "credit_card", "card_last4": "4821", "past_due": 420.0, "days_late": 6, "minimum_payment": 126.0}]}


def _bank(oa, seed):
    b = server.Bank(oa)
    cid = b.session({"seed": seed})[1]["variables"]["customer_id"]
    return b, cid


def _call(b, cid, tool, **body):
    return b.tool(tool, dict(body, customer_id=cid))[1]


def test_g8_defaults_unchanged():
    b, cid = _bank(OA, SEED)
    r = [_call(b, cid, "verify_identity", field="phone_last4", answer="0000") for _ in range(4)]
    assert [x.get("wrong_answers_left") for x in r] == [2, 1, 0, 0] and r[3]["error"] == "identity check is over"


def test_g8_per_question_limit():
    seed = dict(SEED, identity_policy={"max_wrong": 2, "scope": "per_question"})
    b, cid = _bank(OA, seed)
    assert _call(b, cid, "verify_identity", field="date_of_birth", answer="1980-01-20")["wrong_answers_left"] == 1
    assert _call(b, cid, "verify_identity", field="phone_last4", answer="0000")["wrong_answers_left"] == 1
    r = _call(b, cid, "verify_identity", field="phone_last4", answer="4471")
    assert r["verified"] is True and r["all_verified"] is False
    r = _call(b, cid, "verify_identity", field="date_of_birth", answer="1980-01-21")
    assert r["verified"] is False and r["wrong_answers_left"] == 0
    r = _call(b, cid, "verify_identity", field="date_of_birth", answer="1988-03-14")
    assert r["ok"] is False and r["error"] == "identity check is over"
    assert b.ledger(cid)[1]["wrong"] == 3


def test_g8_response_aliases_and_extra_fields():
    seed = dict(SEED, response_aliases={"verify_identity": {"verified": "match", "wrong_answers_left": "attempts_left"}})
    b, cid = _bank(FOA, seed)
    r = _call(b, cid, "verify_identity", field="date_of_birth", answer="1988-03-14")
    assert r["match"] is True and "verified" not in r and r["attempts_left"] == 3
    _call(b, cid, "verify_identity", field="phone_last4", answer="4471")
    r = _call(b, cid, "create_promise", product_id="p1", amount=420, date="2026-10-16")
    assert r["ok"] is False and "channel" in r["error"]
    r = _call(b, cid, "create_promise", product_id="p1", amount=420, date="2026-10-16", channel="cash")
    assert r["ok"] is False and "channel" in r["error"]
    r = _call(b, cid, "create_promise", product_id="p1", amount=420, date="2026-10-16", channel="card")
    assert r["ok"] is True
    led = b.ledger(cid)[1]
    assert led["promises"][0]["channel"] == "card" and led["verified"] == {"date_of_birth": True, "phone_last4": True}


def test_g8_custom_prefix_hint():
    seed = dict(SEED, identity={"postcode": "SW1A 1AA", "phone_last4": "4471"},
                hints={"partial_postcode": {"when": "prefix"}})
    oa = copy.deepcopy(OA)
    oa["paths"]["/verify_identity"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["field"]["enum"] = ["postcode", "phone_last4"]
    b, cid = _bank(oa, seed)
    r = _call(b, cid, "verify_identity", field="postcode", answer="SW1A")
    assert r["hint"] == "partial_postcode" and r["verified"] is False and r["wrong_answers_left"] == 3
    r = _call(b, cid, "verify_identity", field="postcode", answer="sw1a 1aa")
    assert r["verified"] is True


def test_g8_cases_seed_policy_and_aliases():
    s = _aliased(copy.deepcopy(A), {"verify_identity": {"verified": "match"}})
    s["identity"]["max_wrong"] = 2
    s["identity"]["wrong_scope"] = "per_question"
    c = cases.build(s, FOA, "happy_full_today", "2026-10-14T11:00")
    assert c["seed"]["identity_policy"] == {"max_wrong": 2, "scope": "per_question"}
    assert c["seed"]["response_aliases"] == {"verify_identity": {"verified": "match"}}
    c = cases.build(A, OA, "happy_full_today", "2026-10-14T11:00")
    assert c["seed"]["identity_policy"] == {"max_wrong": 3, "scope": "total"} and "response_aliases" not in c["seed"]


# ---------------------------------------------------------------- G5 note reason -> close reason

MOA = json.load(open("bench/multi-raw/openapi.json"))
RENOTE = {"refused": "declined", "dispute": "disputed", "paid": "payment_claimed", "service_error": "system_error"}


def _memo(spec=None):
    """Brief B with the memo's note codes: note reasons renamed, close reasons unchanged."""
    s = copy.deepcopy(spec or B)
    for e in s["events"]:
        if e.get("per_product_note") in RENOTE:
            e["per_product_note"] = RENOTE[e["per_product_note"]]
    n = s["multi_product"]["notes"]
    n["reasons"] = [RENOTE.get(r, r) for r in n["reasons"]]
    n["close_reason_map"] = {"declined": "refused", "disputed": "dispute", "system_error": "service_error"}
    n["paid_note_reason"] = "payment_claimed"
    return s


def test_g5_close_reason_maps_notes():
    m = {"declined": "refused", "disputed": "dispute"}
    cr = dates.close_reason
    assert cr([("p1", "payment_claimed"), ("p2", "disputed"), ("p3", "declined")], 0, None, "first_non_paid_note",
              "paid", None, m, "payment_claimed") == ("no_promise", "dispute")
    assert cr([("p1", "payment_claimed"), ("p2", "payment_claimed")], 0, None, "first_non_paid_note", "paid", None, m,
              "payment_claimed") == ("paid", None)
    assert cr([("p1", "declined")], 0, None, "last_note", "paid", None, m, "payment_claimed") == ("no_promise", "refused")
    # defaults unchanged
    assert cr([("p1", "paid"), ("p2", "refused")], 0, None, "first_non_paid_note") == ("no_promise", "refused")


def test_g5_validate_map():
    assert validate(_memo(), MOA) == []
    bad = _memo()
    bad["multi_product"]["notes"]["close_reason_map"]["declined"] = "nope"
    assert any("nope" in e for e in validate(bad, MOA))
    bad = _memo()
    bad["multi_product"]["notes"]["paid_note_reason"] = "paid"
    assert any("paid_note_reason" in e for e in validate(bad, MOA))


def _wrap(spec, oa, node, notes_state):
    """Run a wrap node with products in the given statuses; return (mp_close, notes written)."""
    w = C(spec, oa)
    prods = [dict(CARD, product_id="p%d" % i, days_late=3) for i in range(1, len(notes_state) + 1)]
    v = {"now": "2026-10-16T11:00", "acct_products_json": json.dumps(prods)}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    st = json.loads(v["mp_state"])
    for pid, stt in notes_state.items():
        st["p"][pid]["status"] = stt
    v["mp_state"] = json.dumps(st)
    exec(_code(w["nodes"][node]), {"vars": v})
    notes = [(v["note%d_pid" % i], v["note%d_reason" % i]) for i in range(1, 4) if v.get("note_has_%d" % i) == "yes"]
    return v["mp_close"], notes, w


def test_g5_wrap_maps_note_to_close_reason():
    s = _memo()
    close, notes, w = _wrap(s, MOA, "wrap_done", {"p1": "payment_claimed", "p2": "disputed", "p3": "declined"})
    assert notes == [("p1", "payment_claimed"), ("p2", "disputed"), ("p3", "declined")]
    assert close == "close_dispute" and w["nodes"][close]
    close, notes, _ = _wrap(s, MOA, "wrap_done", {"p1": "payment_claimed", "p2": "payment_claimed"})
    assert close == "close_paid"
    close, notes, _ = _wrap(s, MOA, "wrap_done", {"p1": "declined", "p2": "declined"})
    assert close == "close_refused"
    assert [p for p in lint(w, s) if not p.startswith("warning:")] == []


def test_g5_oracle_and_cases_use_map():
    s = _memo()
    assert oracle.close_from_notes(s, ["p1", "p2"], {"p1": "payment_claimed", "p2": "declined"}) == ("no_promise", "refused")
    assert oracle.close_from_notes(s, ["p1", "p2"], {"p1": "payment_claimed", "p2": "payment_claimed"}) == ("paid", None)
    c = cases.build(s, MOA, "multi_product", "2026-10-14T11:00", plan=["refused", "paid", "dispute"])
    assert c["expected"]["outcome"] == "no_promise" and c["expected"]["reason"] == "refused", c["expected"]
    assert {n["reason"] for n in c["expected"]["notes"]} == {"declined", "payment_claimed", "disputed"}
    c = cases.build(s, MOA, "multi_product", "2026-10-14T11:00", plan=["paid", "paid", "paid"])
    assert c["expected"]["outcome"] == "paid", c["expected"]


# ---------------------------------------------------------------- G4 response field aliases

ALIASES = {"verify_identity": {"verified": "match", "wrong_answers_left": "attempts_left"}, "create_promise": {"ok": "ok"}}


def _aliased(spec, al=ALIASES):
    s = copy.deepcopy(spec)
    s["responses"] = copy.deepcopy(al)
    return s


def _accepted_oa():
    """create_promise answers {accepted} instead of {ok}."""
    oa = copy.deepcopy(FOA)
    props = oa["paths"]["/create_promise"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    props["accepted"] = props.pop("ok")
    return oa


def test_g4_validate_aliases():
    assert validate(_aliased(A), FOA) == []
    errs = validate(_aliased(A, {"verify_identity": {"verified": "matched"}}), FOA)
    assert any("matched" in e for e in errs), errs
    errs = validate(_aliased(A, {"no_such_tool": {"ok": "ok"}}), FOA)
    assert any("no_such_tool" in e for e in errs), errs


def test_g4_reference_has_no_alias_code():
    assert "RESP" not in json.dumps(C(A, OA)) and "RESP" not in json.dumps(C(B, OA))


def test_g4_verify_posthook_reads_aliases():
    w = C(_aliased(A), FOA)
    post = _named(w, "identity", "verify_identity")["post_process_code"]
    r = {"status": "success", "data": {"ok": True, "match": True, "attempts_left": 3, "all_verified": False}}
    run(post, r)
    assert "This answer is correct" in r["data"]["what_to_do_next"]
    r = {"status": "success", "data": {"ok": True, "match": False, "attempts_left": 2, "all_verified": False}}
    run(post, r)
    assert "SAME question" in r["data"]["what_to_do_next"] and r["data"]["wrong_answers_left"] == 2
    r = {"status": "success", "data": {"ok": False, "match": False, "attempts_left": 0, "error": "identity check is over"}}
    run(post, r)
    assert "ended" in r["data"]["what_to_do_next"] and str(r["data"]["wrong_answers_left"]) == "0"
    for bad in [None, "x", {}, {"data": None}, {"data": {"match": None}}]:
        run(post, copy.deepcopy(bad))


def test_g4_ended_check_is_not_unreadable_without_aliases_either():
    """ok false with wrong_answers_left 0 ends the check (the bank says 'identity check is over')."""
    post = _named(C(A, OA), "identity", "verify_identity")["post_process_code"]
    r = {"status": "success", "data": {"ok": False, "verified": False, "wrong_answers_left": 0, "error": "identity check is over"}}
    run(post, r)
    assert "ended" in r["data"]["what_to_do_next"]


def test_g4_promise_alias_ok():
    s = _aliased(_with_fields(A), {"create_promise": {"ok": "accepted"}})
    oa = _accepted_oa()
    assert validate(s, oa) == []
    w = C(s, oa)
    post = _named(w, "negotiate", "create_promise")["post_process_code"]
    r = {"status": "success", "data": {"accepted": True, "promise_id": "x"}}
    run(post, r)
    assert r["data"]["promise_status"] == "recorded"
    rp = _named(w, "retry_promise", "create_promise")
    r = {"status": "success", "data": {"accepted": True}}
    run(rp["post_process_code"], r)
    assert r["data"]["ok"] is True
    wb = C(_aliased(_with_fields(B), {"create_promise": {"ok": "accepted"}}), oa)
    r = {"status": "success", "data": {"accepted": True}}
    v = {}
    run(wb["nodes"]["record_1"]["pre_actions"][0]["post_process_code"], r, v)
    assert v["rec1_status"] == "recorded"


# ---------------------------------------------------------------- G3 required tool fields the compiler does not know

FOA = json.load(open("bench/fields/openapi.json"))
CH = {"create_promise.channel": {"instruction": "Ask how the customer will pay before the read-back.", "default": "bank_transfer"}}


def _with_fields(spec, tf=CH):
    s = copy.deepcopy(spec)
    s["tool_fields"] = copy.deepcopy(tf)
    return s


def _body(tool):
    return tool["function"]["parameters"]["properties"]["body"]


def _named(w, node, name):
    n = w["nodes"][node]
    return [t for t in n.get("tools", []) + n.get("pre_actions", []) if t["function"]["name"] == name][0]


def test_g3_reference_compile_has_no_extra_fields():
    w = C(A, OA)
    assert "channel" not in json.dumps(w)
    assert "tool_fields" not in A


def test_g3_validate_tool_fields():
    assert validate(_with_fields(A), FOA) == []
    errs = validate(_with_fields(A, {"create_promise.colour": {"instruction": "x"}}), FOA)
    assert any("create_promise.colour" in e for e in errs), errs
    errs = validate(_with_fields(A, {"create_promise.channel": {"instruction": "x", "default": "cash"}}), FOA)
    assert any("cash" in e for e in errs), errs


def test_g3_unknown_required_field_is_model_filled():
    for spec in (A, _with_fields(A)):
        w = C(spec, FOA)
        body = _body(_named(w, "negotiate", "create_promise"))
        ch = body["properties"]["channel"]
        assert "channel" in body["required"]
        assert ch["enum"] == ["bank_transfer", "card", "app"] and ch["default"] == "{{channel}}"
        assert "How the customer will pay" in ch["description"]
    ch = _body(_named(C(_with_fields(A), FOA), "negotiate", "create_promise"))["properties"]["channel"]
    assert CH["create_promise.channel"]["instruction"] in ch["description"] and "bank_transfer" in ch["description"]


def test_g3_prompt_names_the_extra_field():
    t = C(_with_fields(A), FOA)["nodes"]["negotiate"]["task_message"]
    assert "channel" in t and CH["create_promise.channel"]["instruction"] in t
    assert len(t) <= 4600


def test_g3_retry_carries_the_extra_field():
    w = C(_with_fields(A), FOA)
    t = _named(w, "retry_promise", "create_promise")
    assert _body(t)["properties"]["channel"]["default"] == "{{create_promise_args.channel}}"
    cfg = {"body": {"amount": "1", "date": "2026-10-14", "channel": "x"}}
    run(t["pre_process_code"], None, {"create_promise_args.channel": "card", "create_promise_args.promise_amount": 5,
                                      "create_promise_args.promise_date": "2026-10-15"}, cfg)
    assert cfg["body"]["channel"] == "card" and cfg["body"]["amount"] == 5.0
    cfg = {"body": {"amount": "1", "date": "2026-10-14"}}
    run(t["pre_process_code"], None, {}, cfg)
    assert cfg["body"]["channel"] == "bank_transfer"


def test_g3_prehook_maps_bad_value_to_default():
    t = _named(C(_with_fields(A), FOA), "negotiate", "create_promise")
    for sent, want in [("cash", "bank_transfer"), ("Card", "card"), (None, "bank_transfer"), ("app", "app")]:
        cfg = {"body": {"amount": 1, "date": "2026-10-14", "channel": sent}}
        run(t["pre_process_code"], None, {}, cfg)
        assert cfg["body"]["channel"] == want, (sent, cfg)
    for cfg in [{}, {"body": None}, {"body": {}}]:
        run(t["pre_process_code"], None, {}, cfg)


def test_g3_fixed_value_is_baked():
    w = C(_with_fields(A, {"create_promise.channel": {"value": "app"}}), FOA)
    for node in ("negotiate", "retry_promise"):
        ch = _body(_named(w, node, "create_promise"))["properties"]["channel"]
        assert ch["default"] == "app", node


def test_g3_engine_tools_get_default_or_value():
    oa = copy.deepcopy(FOA)
    sc = oa["paths"]["/transfer_to_agent"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    sc["properties"]["priority"] = {"type": "string", "enum": ["low", "high"]}
    sc["required"].append("priority")
    errs = validate(A, oa)
    assert any("transfer_to_agent.priority" in e for e in errs), errs
    s = _with_fields(A, {"transfer_to_agent.priority": {"default": "low"}})
    assert validate(s, oa) == []
    w = C(s, oa)
    t = [n for k, n in w["nodes"].items() if k.startswith("transfer_")][0]["pre_actions"][0]
    assert _body(t)["properties"]["priority"]["default"] == "low"


def test_g3_multi_records_the_extra_field():
    s = _with_fields(B)
    w = C(s, FOA)
    assert [p for p in lint(w, s) if not p.startswith("warning:")] == []
    sp = [t for t in w["nodes"]["negotiate"]["tools"] if t["function"]["name"] == "set_product"][0]
    assert "channel" in sp["function"]["parameters"]["properties"]
    prods = [dict(CARD, days_late=3), dict(CARD, product_id="p2", type="car_loan", past_due=900.0, days_late=3,
                                           instalment_amount=210.0)]
    v = {"now": "2026-10-14T11:00", "title": "Ms", "last_name": "X", "acct_products_json": json.dumps(prods)}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    code = sp["function"]["parameters"]["properties"]["code"]["default"]
    exec(code, {"vars": v, "params": {"product_id": "p1", "outcome": "agreed", "amount": "full", "date": "2026-10-16",
                                      "channel": "card"}})
    exec(code, {"vars": v, "params": {"product_id": "p2", "outcome": "agreed", "amount": "full", "date": "2026-10-16",
                                      "channel": "cash"}})
    assert v["mp_phase"] == "readback", v["mp_msg"]
    assert "card" in v["mp_readback"] and "bank transfer" in v["mp_readback"]
    exec(_code(w["nodes"]["record_plan"]), {"vars": v})
    assert v["rec1_channel"] == "card" and v["rec2_channel"] == "bank_transfer"
    for node, want in (("record_1", "card"), ("retry_2", "bank_transfer")):
        t = w["nodes"][node]["pre_actions"][0]
        assert _body(t)["properties"]["channel"]["default"].startswith("{{rec")
        cfg = {"body": {}}
        run(t["pre_process_code"], None, v, cfg)
        assert cfg["body"]["channel"] == want

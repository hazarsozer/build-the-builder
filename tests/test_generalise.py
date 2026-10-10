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

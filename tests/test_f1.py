"""F1 (MRW-04): the model reported set_product outcome `refused` for every product of a brief whose refusal note
is named differently (`declined`); the wrap wrote no note. A product status that means a refusal is noted with the
brief's refusal note reason."""
import json

import pytest

from builder.compile import compile as C

OA = json.load(open("tools/openapi.json"))
B = json.load(open("specs/reference/spec-b.json"))
MRW = json.load(open("tests/fixtures/bench-multi-raw.spec.json"))
MRW_OA = json.load(open("bench/multi-raw/openapi.json"))
CASES = {c["id"]: c for c in json.load(open("public-test-set.json"))}
URL = "https://bank.example/v1"
MRW_PRODS = [{"product_id": "p1", "type": "credit_card", "card_last4": "5590", "past_due": 400.0, "days_late": 2, "minimum_payment": 120.0},
             {"product_id": "p2", "type": "personal_loan", "past_due": 1100.0, "days_late": 15, "instalment_amount": 275.0}]


def _code(node):
    return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def _notes(spec, oa, prods, statuses, node="wrap_done", now="2026-11-20T13:30"):
    w = C(spec, oa, base_url=URL)
    v = {"now": now, "acct_products_json": json.dumps(prods)}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    st = json.loads(v["mp_state"])
    for pid, stt in statuses.items():
        st["p"][pid]["status"] = stt
    v["mp_state"] = json.dumps(st)
    exec(_code(w["nodes"]["record_plan"]), {"vars": v})
    exec(_code(w["nodes"][node]), {"vars": v})
    out, i = [], 1
    while v.get("note_has_%d" % i) == "yes":
        out.append((v["note%d_pid" % i], v["note%d_reason" % i]))
        i += 1
    return out


@pytest.mark.parametrize("node", ["wrap_done", "wrap_recorded"])
def test_mrw04_refused_statuses_are_noted_declined(node):
    assert _notes(MRW, MRW_OA, MRW_PRODS, {"p1": "refused", "p2": "refused"}, node) == [("p1", "declined"), ("p2", "declined")]


def test_mrw_declined_status_still_noted_declined():
    assert _notes(MRW, MRW_OA, MRW_PRODS, {"p1": "declined", "p2": "refused"}) == [("p1", "declined"), ("p2", "declined")]


def test_mrw_other_statuses_unchanged():
    assert _notes(MRW, MRW_OA, MRW_PRODS, {"p1": "disputed", "p2": "refused"}) == [("p1", "disputed"), ("p2", "declined")]


def test_spec_b_refused_statuses_still_noted_refused():
    prods = [dict(p, product_id="p%d" % (i + 1)) for i, p in enumerate(CASES["PUB-13"]["products"])]
    assert _notes(B, OA, prods, {"p1": "refused", "p2": "refused"}, now="2026-10-14T10:00") == [("p1", "refused"), ("p2", "refused")]

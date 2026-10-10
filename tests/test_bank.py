"""Our mock bank: behaviour copied from the official mock (probed 2026-10-10)."""
import json, pathlib, sys, urllib.error, urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bank import server  # noqa: E402

OPENAPI = json.loads((ROOT / "tools" / "openapi.json").read_text())
PUB01 = {"variables": {"first_name": "A", "last_name": "B", "title": "Mr", "now": "2026-10-14T11:00"},
         "identity": {"date_of_birth": "1988-03-14", "phone_last4": "4471"},
         "products": [{"type": "credit_card", "card_last4": "4821", "past_due": 420.0, "days_late": 6, "minimum_payment": 126.0}]}


class Bank:
    def __init__(self, base): self.base = base

    def raw(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"content-type": "application/json"}, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as r: return r.status, json.load(r)
        except urllib.error.HTTPError as e: return e.code, json.load(e)

    def seed(self, seed): return self.raw("/v1/practice/sessions", {"seed": seed})[1]["variables"]
    def call(self, tool, **body): return self.raw("/v1/" + tool, body)[1]
    def ledger(self, cid): return self.raw("/v1/practice/ledger/" + cid)[1]

    def verified(self, seed=PUB01):
        cid = self.seed(seed)["customer_id"]
        self.call("verify_identity", customer_id=cid, field="date_of_birth", answer=seed["identity"]["date_of_birth"])
        self.call("verify_identity", customer_id=cid, field="phone_last4", answer=seed["identity"]["phone_last4"])
        return cid


@pytest.fixture(scope="module")
def bank():
    srv = server.serve(0, OPENAPI)
    yield Bank(f"http://127.0.0.1:{srv.server_address[1]}")
    srv.shutdown()


def test_verify_flow_and_ledger(bank):
    v = bank.seed(PUB01)
    cid = v["customer_id"]
    assert v["first_name"] == "A" and v["now"] == "2026-10-14T11:00" and cid.startswith("c_")
    assert bank.call("get_account", customer_id=cid)["error"] == "identity not verified"
    r = bank.call("verify_identity", customer_id=cid, field="date_of_birth", answer="1988-14-03")
    assert r.get("hint") == "day_month_swapped"
    r = bank.call("verify_identity", customer_id=cid, field="date_of_birth", answer="1988-03-15")
    assert r["verified"] is False and r["wrong_answers_left"] == 2
    bank.call("verify_identity", customer_id=cid, field="date_of_birth", answer="1988-03-14")
    r = bank.call("verify_identity", customer_id=cid, field="phone_last4", answer="4471")
    assert r["all_verified"] is True
    assert bank.call("get_account", customer_id=cid)["products"][0]["product_id"] == "p1"
    bank.call("create_promise", customer_id=cid, product_id="p1", amount=420, date="2026-10-14")
    bank.call("close_call", customer_id=cid, outcome="promise")
    led = bank.ledger(cid)
    assert led["outcome"] == "promise" and led["promises"][0]["amount"] == 420 and led["calls"][-1]["tool"] == "close_call"


def test_exact_official_shapes(bank):
    cid = bank.seed(PUB01)["customer_id"]
    c = lambda **b: bank.call("verify_identity", customer_id=cid, **b)
    assert c(field="date_of_birth", answer="1988-14-03") == {"ok": True, "verified": False, "wrong_answers_left": 3, "hint": "day_month_swapped"}
    # the hint is given once per call; a second swap is a wrong answer
    assert c(field="date_of_birth", answer="1988-14-03") == {"ok": True, "verified": False, "wrong_answers_left": 2, "all_verified": False}
    assert c(field="date_of_birth", answer="abc") == {"ok": False, "error": "answer is not a date. Send it as YYYY-MM-DD."}
    assert c(field="postcode", answer="1") == {"ok": False, "error": "field must be date_of_birth or phone_last4"}
    assert c(field="date_of_birth", answer="14 March 1988") == {"ok": True, "verified": True, "wrong_answers_left": 2, "all_verified": False}
    # a field already verified is not checked again, and all_verified is left out
    assert c(field="date_of_birth", answer="1999-01-01") == {"ok": True, "verified": True, "wrong_answers_left": 2}
    assert c(field="phone_last4", answer="447") == {"ok": True, "verified": False, "wrong_answers_left": 1, "all_verified": False}
    assert c(field="phone_last4", answer="4472") == {"ok": True, "verified": False, "wrong_answers_left": 0, "all_verified": False}
    assert c(field="phone_last4", answer="4471") == {"ok": False, "verified": False, "wrong_answers_left": 0, "error": "identity check is over"}
    assert bank.call("get_account", customer_id=cid) == {"ok": False, "error": "identity not verified"}
    assert bank.call("close_call", customer_id=cid, outcome="not_verified", reason="failed") == {"ok": True}
    assert bank.call("close_call", customer_id=cid, outcome="not_verified", reason="failed") == {"ok": False, "error": "the call is already closed"}
    assert bank.call("add_note", customer_id=cid, product_id="p1", reason="refused") == {"ok": False, "error": "the call is already closed"}
    led = bank.ledger(cid)
    assert (led["wrong"], led["hinted"], led["verified"]) == (3, True, {"date_of_birth": True})
    assert led["calls"][0] == {"n": 1, "tool": "verify_identity", "args": {"field": "date_of_birth", "answer": "1988-14-03"}, "status": 200,
                               "result": {"ok": True, "verified": False, "wrong_answers_left": 3, "hint": "day_month_swapped"}}


@pytest.mark.parametrize("answer,kind", [
    ("14 March 1988", "ok"), ("March 14, 1988", "ok"), ("1988/03/14", "ok"), ("14.03.1988", "ok"), ("14-03-1988", "ok"),
    ("1988-3-14", "ok"), ("14th of March 1988", "ok"), ("1988-03-14T00:00", "ok"), (" 1988-03-14 ", "ok"), ("Mar 14 1988", "ok"),
    ("14/03/1988", "ok"), ("1988-03-14 maybe", "ok"),
    ("03-14-1988", "swap"), ("1988-14-03", "swap"), ("03/14/1988", "swap"),
    ("1988-13-40", "wrong"), ("1988-02-30", "wrong"), ("14/03/1989", "wrong"),
    ("19880314", "unreadable"), ("fourteenth of March nineteen eighty-eight", "unreadable"), ("14/3/88", "unreadable"),
    ("1988", "unreadable"), ("next tuesday", "unreadable"), ("", "unreadable"), (19880314, "unreadable"),
])
def test_date_formats(bank, answer, kind):
    cid = bank.seed(PUB01)["customer_id"]
    r = bank.call("verify_identity", customer_id=cid, field="date_of_birth", answer=answer)
    want = {"ok": {"ok": True, "verified": True, "wrong_answers_left": 3, "all_verified": False},
            "swap": {"ok": True, "verified": False, "wrong_answers_left": 3, "hint": "day_month_swapped"},
            "wrong": {"ok": True, "verified": False, "wrong_answers_left": 2, "all_verified": False},
            "unreadable": {"ok": False, "error": "answer is not a date. Send it as YYYY-MM-DD."}}[kind]
    assert r == want


@pytest.mark.parametrize("answer,ok", [("4471", True), ("44 71", True), ("4,471", True), ("four four seven one", False), ("447", False),
                                       ("44711", False), ("x4471", False), ("+1 555 4471", False), ("", False), (4471, True)])
def test_phone_formats(bank, answer, ok):
    cid = bank.seed(PUB01)["customer_id"]
    r = bank.call("verify_identity", customer_id=cid, field="phone_last4", answer=answer)
    assert r["verified"] is ok and r["wrong_answers_left"] == (3 if ok else 2)


def test_other_tools_and_validation(bank):
    cid = bank.verified()
    assert bank.call("get_account", customer_id=cid) == {"ok": True, "products": [
        {"product_id": "p1", "type": "credit_card", "card_last4": "4821", "past_due": 420, "days_late": 6, "minimum_payment": 126}]}
    p = lambda **b: bank.call("create_promise", customer_id=cid, **b)
    assert p(product_id="p9", amount="abc", date="x") == {"ok": False, "error": "unknown product_id"}
    assert p(product_id="p1", amount=0, date="2026-10-15") == {"ok": False, "error": "amount must be a positive number"}
    assert p(product_id="p1", amount=5, date="2026-1-15") == {"ok": False, "error": "date must be YYYY-MM-DD"}
    assert p(product_id="p1", amount="420.50", date="2026-10-15") == {"ok": True, "promise_id": "pr1"}
    assert p(product_id="p1", amount=1, date="2026-10-16") == {"ok": False, "error": "this product already has a promise"}
    assert bank.call("add_note", customer_id=cid, product_id="p1", reason="bogus") == {"ok": False, "error": "unknown reason"}
    assert bank.call("add_note", customer_id=cid, reason="refused") == {"ok": True}
    assert bank.call("schedule_callback", customer_id=cid, date="2026-10-15", time="9:00") == {"ok": False, "error": "date must be YYYY-MM-DD and time HH:MM"}
    assert bank.call("schedule_callback", customer_id=cid, date="2026-10-15", time="11:00") == {"ok": True}
    assert bank.call("transfer_to_agent", customer_id=cid, queue="bogus") == {"ok": False, "error": "unknown queue"}
    assert bank.call("transfer_to_agent", customer_id=cid, queue="general") == {"ok": True}
    cc = lambda **b: bank.call("close_call", customer_id=cid, **b)
    assert cc(outcome="bogus") == {"ok": False, "error": "unknown outcome"}
    assert cc(outcome="promise", reason="x") == {"ok": False, "error": "this outcome needs one of these reasons: "}
    assert cc(outcome="no_promise") == {"ok": False, "error": "this outcome needs one of these reasons: refused, hardship, dispute, service_error"}
    assert cc(outcome="transferred", reason="human") == {"ok": True}
    led = bank.ledger(cid)
    assert led["promises"] == [{"promise_id": "pr1", "product_id": "p1", "type": "credit_card", "amount": 420.5, "date": "2026-10-15"}]
    assert led["notes"] == [{"product_id": None, "reason": "refused", "text": ""}]
    assert led["callbacks"] == [{"date": "2026-10-15", "time": "11:00"}] and led["transfers"] == [{"queue": "general"}]
    assert (led["outcome"], led["reason"]) == ("transferred", "human")


def test_unknown_customer_and_route(bank):
    assert bank.raw("/v1/get_account", {"customer_id": "c_nope"}) == (404, {"ok": False, "error": "unknown customer_id"})
    assert bank.raw("/v1/nope", {"customer_id": "c_nope"}) == (404, {"ok": False, "error": "not found"})
    assert bank.raw("/v1/practice/ledger/c_nope")[0] == 404


def test_fault_injection(bank):
    seed = dict(PUB01, faults={"create_promise": [1, 2]})
    cid = bank.verified(seed)
    for _ in range(2):
        assert bank.raw("/v1/create_promise", {"customer_id": cid, "product_id": "p1", "amount": 420, "date": "2026-10-14"}) == \
            (500, {"ok": False, "error": "service unavailable, nothing was recorded"})
    assert bank.ledger(cid)["promises"] == []
    assert bank.call("create_promise", customer_id=cid, product_id="p1", amount=420, date="2026-10-14")["ok"] is True
    led = bank.ledger(cid)
    assert len(led["promises"]) == 1 and led["failures"]["create_promise"] == 2 and [c["status"] for c in led["calls"][-3:]] == [500, 500, 200]


def test_fault_fires_before_identity_check(bank):
    cid = bank.seed(dict(PUB01, faults={"create_promise": [1]}))["customer_id"]
    assert bank.raw("/v1/create_promise", {"customer_id": cid, "product_id": "p1", "amount": 1, "date": "2026-10-14"})[0] == 500


def test_seed_from_public_case(bank):
    v = bank.raw("/v1/practice/sessions", {"case": "PUB-10"})[1]["variables"]
    assert v["first_name"] == "Grace"
    assert bank.raw("/v1/create_promise", {"customer_id": v["customer_id"], "product_id": "p1", "amount": 1, "date": "2026-10-14"})[0] == 500
    assert bank.raw("/v1/practice/sessions", {"case": "PUB-99"}) == (404, {"ok": False, "error": "unknown practice case"})


def test_conformance_replay_and_diff():
    sys.path.insert(0, str(ROOT / "tools"))
    import conformance
    srv = server.serve(0, OPENAPI)
    try:
        b = srv.bank
        cid = b.session({"case": "PUB-10"})[1]["variables"]["customer_id"]
        for tool, args in [("verify_identity", {"field": "date_of_birth", "answer": "1989-02-23"}), ("verify_identity", {"field": "phone_last4", "answer": "9904"}),
                           ("create_promise", {"product_id": "p1", "amount": 365, "date": "2026-10-16"}),
                           ("create_promise", {"product_id": "p1", "amount": 365, "date": "2026-10-16"}), ("close_call", {"outcome": "promise"})]:
            b.tool(tool, {"customer_id": cid, **args})
        led = b.ledger(cid)[1]
        assert conformance.replay("PUB-10", led["calls"], b) == []
        led["calls"][2]["status"] = 200            # a recorded answer the bank would not give again
        assert [m[0] for m in conformance.replay("PUB-10", led["calls"], b)] == [3]
        other = dict(led, outcome="no_promise", promises=[])
        assert set(conformance.diff(conformance.scored(led), conformance.scored(other))) == {"outcome", "promises"}
    finally:
        srv.shutdown()


def test_tunnel_parses_url():
    from bank import tunnel
    line = "2026-10-10T12:00:00Z INF |  https://abc-def-ghi.trycloudflare.com                                  |"
    assert tunnel.parse_url(line) == "https://abc-def-ghi.trycloudflare.com"
    assert tunnel.parse_url("INF Requesting new quick Tunnel on trycloudflare.com...") is None

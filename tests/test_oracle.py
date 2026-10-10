import copy
import json
from pathlib import Path

from builder.oracle import check, close_from_notes

ROOT = Path(__file__).resolve().parent.parent
A = json.load(open(ROOT / "specs/reference/spec-a.json"))
B = json.load(open(ROOT / "specs/reference/spec-b.json"))

CARD = {"product_id": "p1", "type": "credit_card", "card_last4": "4821", "past_due": 420.0,
        "days_late": 20, "minimum_payment": 35.0}
LOAN = {"product_id": "p2", "type": "personal_loan", "past_due": 300.0, "days_late": 5,
        "instalment_amount": 150.0}


def sc(**kw):
    base = {"id": "s", "now": "2026-10-14T11:00", "products": [CARD], "customer_says": "...",
            "inputs": {}, "expected": {"outcome": "promise", "reason": None, "promises": [], "notes": [],
                                       "callbacks": [], "transfers": []},
            "quote": "q"}
    exp = kw.pop("expected", {})
    base.update(kw)
    base["expected"] = dict(base["expected"], **exp)
    return base


def fields(ms):
    return sorted(m["field"] for m in ms)


# --- deadlines -----------------------------------------------------------------

def test_correct_deadline_no_mismatch():
    s = sc(expected={"deadlines": {"p1": "2026-10-23"}})
    assert check(A, [s]) == []


def test_memo_changed_deadline_is_flagged_on_the_band():
    # A scenario written from a memo saying the late band is 5 business days.
    s = sc(expected={"deadlines": {"p1": "2026-10-21"}})
    ms = check(A, [s])
    assert fields(ms) == ["deadline"]
    assert ms[0]["spec_paths"] == ["bands[1].deadline"]
    assert ms[0]["computed"] == "2026-10-23" and ms[0]["expected"] == "2026-10-21"


def test_early_band_deadline_over_holiday():
    s = sc(products=[LOAN], expected={"deadlines": {"p2": "2026-11-02"}})
    assert check(A, [s]) == []


# --- persuasion offers -----------------------------------------------------------

def test_offers_late_band_ok():
    s = sc(inputs={"refusals": 3}, expected={
        "outcome": "no_promise", "reason": "refused",
        "offers": [{"product_id": "p1", "attempt": 1, "amount": 420.0, "date": "2026-10-16"},
                   {"product_id": "p1", "attempt": 2, "amount": 35.0, "date": "2026-10-23"}]})
    assert check(A, [s]) == []


def test_offer_amount_uses_floor_after_too_much():
    s = sc(inputs={"too_much": True}, expected={
        "offers": [{"product_id": "p1", "attempt": 1, "amount": 35.0, "date": "2026-10-16"}]})
    assert check(A, [s]) == []
    s["inputs"] = {}
    assert fields(check(A, [s])) == ["offer amount"]


def test_offer_wrong_date_flagged():
    s = sc(expected={"offers": [{"product_id": "p1", "attempt": 1, "amount": 420.0, "date": "2026-10-23"}]})
    ms = check(A, [s])
    assert fields(ms) == ["offer date"]
    assert ms[0]["spec_paths"] == ["bands[1].attempts[0].date"]


def test_more_offers_than_attempts_flagged():
    s = sc(products=[LOAN], expected={
        "outcome": "no_promise", "reason": "refused",
        "offers": [{"product_id": "p2", "attempt": 1, "amount": 300.0, "date": "2026-10-16"},
                   {"product_id": "p2", "attempt": 2, "amount": 150.0, "date": "2026-11-02"}]})
    assert "attempt count" in fields(check(A, [s]))


def test_fewer_offers_than_attempts_on_refusal_flagged():
    s = sc(inputs={"refusals": 5}, expected={
        "outcome": "no_promise", "reason": "refused",
        "offers": [{"product_id": "p1", "attempt": 1, "amount": 420.0, "date": "2026-10-16"}]})
    assert fields(check(A, [s])) == ["attempt count"]


# --- customer-named dates ---------------------------------------------------------

def test_weekend_date_snaps_to_next_business_day():
    s = sc(inputs={"customer_date": "2026-10-17"},
           expected={"promises": [{"product_id": "p1", "amount": 420.0, "date": "2026-10-19"}]})
    assert check(A, [s]) == []
    s["expected"]["promises"][0]["date"] = "2026-10-16"
    assert fields(check(A, [s])) == ["promise date"]


def test_holiday_and_after_deadline():
    hol = sc(now="2026-10-21T11:00", inputs={"customer_date": "2026-10-26"},
             expected={"promises": [{"product_id": "p1", "amount": 420.0, "date": "2026-10-27"}]})
    late = sc(inputs={"customer_date": "2026-11-20"},
              expected={"promises": [{"product_id": "p1", "amount": 420.0, "date": "2026-10-23"}]})
    assert check(A, [hol, late]) == []


def test_promise_below_floor_flagged():
    s = sc(expected={"promises": [{"product_id": "p1", "amount": 20.0, "date": "2026-10-16"}]})
    assert fields(check(A, [s])) == ["promise amount"]


# --- outcomes, transfers, callbacks --------------------------------------------------

def test_unknown_outcome_pair_flagged():
    s = sc(expected={"outcome": "no_promise", "reason": "too_busy"})
    ms = check(A, [s])
    assert fields(ms) == ["outcome"]
    assert ms[0]["spec_paths"] == ["outcomes"]


def test_unknown_transfer_queue_flagged():
    s = sc(expected={"outcome": "transferred", "reason": "human", "transfers": ["vip"]})
    assert fields(check(A, [s])) == ["transfer queue"]


def test_callback_window():
    inside = sc(inputs={"callback_at": "2026-10-14T19:00"},
                expected={"outcome": "not_reached", "reason": "callback",
                          "callbacks": [{"date": "2026-10-14", "time": "19:00"}]})
    next_bd = sc(inputs={"callback_at": "2026-10-15T10:00"},
                 expected={"outcome": "not_reached", "reason": "callback",
                           "callbacks": [{"date": "2026-10-15", "time": "10:00"}]})
    assert check(A, [inside, next_bd]) == []
    outside = sc(inputs={"callback_at": "2026-10-16T12:00"},
                 expected={"outcome": "not_reached", "reason": "callback",
                           "callbacks": [{"date": "2026-10-16", "time": "12:00"}]})
    before_now = sc(inputs={"callback_at": "2026-10-14T10:30"},
                    expected={"outcome": "not_reached", "reason": "no_callback", "callbacks": []})
    assert fields(check(A, [outside])) == ["callback window"]
    assert check(A, [before_now]) == []


# --- multi-product close ------------------------------------------------------------

def test_close_from_notes_rules():
    order = ["p1", "p2", "p3"]
    assert close_from_notes(B, order, {"p1": "paid", "p2": "dispute", "p3": "refused"}) == ("no_promise", "dispute")
    assert close_from_notes(B, order, {"p1": "paid", "p2": "paid", "p3": "paid"}) == ("paid", None)


def test_multi_close_checked():
    p3 = dict(CARD, product_id="p3", days_late=3)
    s = sc(products=[CARD, LOAN, p3], expected={
        "outcome": "no_promise", "reason": "refused",
        "notes": [{"product_id": "p1", "reason": "paid"}, {"product_id": "p2", "reason": "dispute"},
                  {"product_id": "p3", "reason": "refused"}]})
    ms = check(B, [s])
    assert fields(ms) == ["close reason"]
    assert ms[0]["computed"] == ["no_promise", "dispute"]
    s["expected"]["reason"] = "dispute"
    assert check(B, [s]) == []


def test_notes_when_spec_disables_notes_flagged():
    s = sc(products=[CARD, LOAN], expected={
        "outcome": "no_promise", "reason": "dispute",
        "notes": [{"product_id": "p1", "reason": "dispute"}, {"product_id": "p2", "reason": "dispute"}]})
    ms = check(A, [s])
    assert "notes" in fields(ms)


def test_bad_scenario_reported_not_raised():
    s = sc(now="not a date", expected={"deadlines": {"p1": "2026-10-23"}})
    ms = check(A, [s])
    assert fields(ms) == ["scenario"]

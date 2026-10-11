"""A21: cut-off fragments and small talk (talkative / pieces styles, results/diag-styles.md).

Offline only. Where the platform decides by extraction and routing, the diag transcripts' turn sequences are replayed
through the compiled graph (code nodes run for real, conversation transitions evaluated in order, as in test_a10)."""
import json

from builder.compile import CUT_OFF, NEG_CUT_OFF, compile as C
from tests.test_a10 import _code, holds, route  # noqa: F401  (offline engine helpers)

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
URL = "https://bank.example/v1"


def wf(spec=A):
    return C(spec, OA, base_url=URL)


# ---------------------------------------------------------------- 2 + 6. negotiate: small talk, "Go on." once

def test_negotiate_small_talk_is_no_refusal_and_reasks_without_a_figure():
    for spec in (A, B):
        t = wf(spec)["nodes"]["negotiate"]["task_message"]
        assert NEG_CUT_OFF in t
        assert "small talk with nothing about paying is not an answer or a refusal and uses no attempt" in t
        assert 'no amount or date ("So, can you do that?")' in t
        # the refusal rule no longer counts any non-answer (PUB-05: small talk used up the plan)
        assert "non-answer" not in t
        assert 'an evasive reply about paying' in t and '"I can\'t", "not now"' in t


def test_negotiate_go_on_never_twice_in_a_row():
    for spec in (A, B):
        t = wf(spec)["nodes"]["negotiate"]["task_message"]
        assert 'say only "Go on."' in t and "never twice in a row" in t


def test_negotiate_keeps_a9_a12_rules():
    t = wf(A)["nodes"]["negotiate"]["task_message"]
    assert '"no" to paying today with no date and no amount' in t and "Never repeat an offer the customer already refused." in t
    assert "Their own date, even after a \"no\", is never a refusal." in t and "never ask them to name an amount" in t
    m = wf(B)["nodes"]["negotiate"]["task_message"]
    assert "call set_product with outcome refused at once, before you speak" in m
    assert 'A "no" to the today question uses no attempt' in m


def test_negotiate_small_talk_is_never_cannot_pay():
    for spec in (A, B):
        d = wf(spec)["nodes"]["negotiate"]["extraction_schema"][0]["description"]
        assert "or only small talk is never cannot_pay_or_too_much: it is continue." in d


def test_negotiate_sizes():
    for bu in (None, URL):
        assert len(C(A, OA, base_url=bu)["nodes"]["negotiate"]["task_message"]) <= 4800
        assert len(C(B, OA, base_url=bu)["nodes"]["negotiate"]["task_message"]) <= 5800


def test_generic_cut_off_reasks_after_one_go_on():
    assert 'say only "Go on."' in CUT_OFF and 'Never twice in a row: after your own "Go on.", ask your last question again' in CUT_OFF

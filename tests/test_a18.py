"""Task A18: verbatim opening vs reconcile, one sympathy sentence, multi-product restructuring, never-say words.
Fixtures are the 11 Oct bench run's extracted specs (outcomes, multi-raw, strict-wording)."""
import copy
import json

from builder import cases
from builder.compile import compile as C
from builder.lint import lint, reconcile_banned, required_pre_verify_sentences

INS = json.load(open("tests/fixtures/bench-insurance.spec.json"))
INS_OA = json.load(open("bench/insurance/openapi.json"))
OUT = json.load(open("tests/fixtures/bench-outcomes.spec.json"))
OUT_OA = json.load(open("bench/outcomes/openapi.json"))
MRW = json.load(open("tests/fixtures/bench-multi-raw.spec.json"))
MRW_OA = json.load(open("bench/multi-raw/openapi.json"))
OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
URL = "https://bank.example/v1"


# ---- 1. A13 reconcile x A16 verbatim

def _ins_recorded(verbatim):
    s = copy.deepcopy(INS)
    s["opening"]["verbatim"] = verbatim
    s["opening"]["banned_before_verify"] = s["opening"]["banned_before_verify"] + ["recorded"]
    return s


def test_reconcile_reads_the_verbatim_first_sentence_unchanged():
    s = _ins_recorded(True)
    first = dict(required_pre_verify_sentences(s))["opening.first_sentence"]
    assert first == s["opening"]["first_sentence"]
    new, removed = reconcile_banned(s)
    assert [r["term"] for r in removed] == ["recorded"]
    assert "recorded" not in new["opening"]["banned_before_verify"]


def test_reconcile_reads_the_rephrased_sentence_without_verbatim():
    s = _ins_recorded(False)
    first = dict(required_pre_verify_sentences(s))["opening.first_sentence"]
    assert "recorded" not in first and "monitored and stored" in first
    _, removed = reconcile_banned(s)
    assert removed == []


def _ctx(spec):
    c = object.__new__(cases.Ctx)
    c.spec = spec
    return c


def test_case_check_never_bans_a_word_of_the_verbatim_opening():
    s = _ins_recorded(True)
    (line,) = _ctx(s).banned()
    assert "recorded" not in line and "premium" in line
    (line,) = _ctx(_ins_recorded(False)).banned()
    assert "recorded" in line


def test_case_check_unchanged_for_reference_specs():
    for s in (A, B):
        b = s["opening"]["banned_before_verify"]
        assert _ctx(s).banned() == ([f"none of these is said before identity is verified: {', '.join(b)}"] if b else [])


# ---- 2. one sentence of sympathy

def _hold(spec, oa, ev="hardship"):
    return C(spec, oa, base_url=URL)["nodes"][ev + "_hold"]["task_message"]


def test_fixed_sympathy_sentence_is_the_only_one():
    t = _hold(OUT, OUT_OA)
    assert "I am sorry to hear that." in t
    assert "Exactly one short sentence of sympathy" not in t
    assert "This is your one sentence of sympathy" in t
    # the fixed sentence already asks the yes/no question: no second offer step
    assert "as a yes/no question" not in t


def test_without_a_fixed_sentence_the_prompt_still_asks_for_one_sympathy_sentence():
    s = copy.deepcopy(OUT)
    for e in s["events"]:
        if e["id"] == "hardship":
            e["say"] = None
    t = _hold(s, OUT_OA)
    assert "Exactly one short sentence of sympathy." in t and "as a yes/no question" in t
    assert "This is your one sentence of sympathy" not in t


def test_fixed_sentence_without_sympathy_keeps_the_sympathy_step():
    s = copy.deepcopy(OUT)
    for e in s["events"]:
        if e["id"] == "hardship":
            e["say"] = "Would you like me to connect you with our customer support team?"
    t = _hold(s, OUT_OA)
    assert "Exactly one short sentence of sympathy." in t and "This is your one sentence" not in t


def test_multi_fixed_sympathy_sentence_without_question_keeps_the_offer_step():
    t = _hold(MRW, MRW_OA)
    assert "I am sorry to hear that." in t and "Exactly one short sentence of sympathy" not in t
    assert "as a yes/no question" in t


# ---- 3. multi-product restructuring

def _neg_desc(spec, oa):
    return C(spec, oa, base_url=URL)["nodes"]["negotiate"]["extraction_schema"][0]["description"]


def test_accepting_an_offered_amount_is_not_a_transfer_request():
    d = _neg_desc(MRW, MRW_OA)
    assert "Accepting an amount or a date the agent offered" in d and "is NOT restructuring: it is continue." in d

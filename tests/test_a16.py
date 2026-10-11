"""Task A16: opening.verbatim keeps the first sentence byte-exact."""
import copy
import json

from builder.compile import compile as C
from builder.lint import lint

INS = json.load(open("tests/fixtures/bench-insurance.spec.json"))
INS_OA = json.load(open("bench/insurance/openapi.json"))
OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
WORD = ("Hello, this is the digital assistant of Brightwater Mutual Insurance. "
        "This call is recorded for quality and training.")


def _ins(verbatim):
    s = copy.deepcopy(INS)
    assert s["opening"]["first_sentence"] == WORD
    if verbatim is None:
        s["opening"].pop("verbatim", None)
    else:
        s["opening"]["verbatim"] = verbatim
    return s


def test_verbatim_opening_is_kept_byte_exact():
    s = _ins(True)
    w = C(s, INS_OA, base_url="https://bank.example/v1")
    assert w["nodes"]["opening"]["on_enter"]["text"].startswith(WORD + " ")
    errs = lint(w, s)
    assert [e for e in errs if not e.startswith("warning:")] == []
    assert any(e.startswith("warning:") and "recorded" in e for e in errs)


def test_non_verbatim_opening_is_still_rewritten():
    for flag in (None, False):
        s = _ins(flag)
        t = C(s, INS_OA, base_url="https://bank.example/v1")["nodes"]["opening"]["on_enter"]["text"]
        assert "recorded" not in t and "monitored and stored" in t


def test_verbatim_only_exempts_the_first_sentence():
    s = _ins(True)
    s["opening"]["confirm_question"] = "Is the premium paid, {{title}} {{last_name}}?"
    w = C(s, INS_OA, base_url="https://bank.example/v1")
    assert any("premium" in e and not e.startswith("warning:") for e in lint(w, s))


def test_reference_specs_unchanged_by_flag_absence():
    for s in (A, B):
        assert "verbatim" not in s["opening"]
        w = C(s, OA)
        assert lint(w, s) == []

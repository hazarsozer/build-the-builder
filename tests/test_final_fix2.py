"""Second whole-branch review fixes (final-review-2.md): I1-I5 and M1. Offline only."""
import copy
import json

from builder.compile import compile as C
from builder.lint import lint

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
INS = json.load(open("tests/fixtures/bench-insurance.spec.json"))
INS_OA = json.load(open("bench/insurance/openapi.json"))
URL = "https://bank.example/v1"


def _errors(w, s):
    return [e for e in lint(w, s) if not e.startswith("warning:")]


# ---------------------------------------------------------------- I1: a placeholder in the first sentence

def test_i1_first_sentence_placeholder_is_rendered():
    for base in (A, B):
        s = copy.deepcopy(base)
        s["opening"]["first_sentence"] = "Hello {first_name}, this is a digital assistant calling from the bank."
        w = C(s, OA, base_url=URL)
        t = w["nodes"]["opening"]["on_enter"]["text"]
        assert t.startswith("Hello {{first_name}}, this is")
        assert "{first_name}" not in t.replace("{{first_name}}", "")
        assert _errors(w, s) == []


def test_i1_verbatim_first_sentence_with_placeholder_stays_exempt():
    s = copy.deepcopy(INS)
    s["opening"]["verbatim"] = True
    s["opening"]["first_sentence"] = "Hello {first_name}, this is Brightwater. This call is recorded for quality."
    w = C(s, INS_OA, base_url=URL)
    assert w["nodes"]["opening"]["on_enter"]["text"].startswith(
        "Hello {{first_name}}, this is Brightwater. This call is recorded for quality. ")
    errs = lint(w, s)
    assert [e for e in errs if not e.startswith("warning:")] == []
    assert any(e.startswith("warning:") and "recorded" in e for e in errs)


def test_i1_lint_flags_a_raw_placeholder_in_spoken_text():
    w = C(A, OA, base_url=URL)
    w["nodes"]["opening"]["on_enter"]["text"] = "Hello {first_name}. " + w["nodes"]["opening"]["on_enter"]["text"]
    assert any("raw placeholder {first_name}" in e for e in lint(w, A))


def test_i1_reference_specs_have_no_raw_placeholder():
    for s in (A, B):
        assert not any("raw placeholder" in e for e in lint(C(s, OA, base_url=URL), s))

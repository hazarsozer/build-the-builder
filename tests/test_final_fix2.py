"""Second whole-branch review fixes (final-review-2.md): I1-I5 and M1. Offline only."""
import copy
import json

from builder.compile import compile as C
from builder.lint import lint
from tests.test_a10 import _code

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


# ---------------------------------------------------------------- I2: events[].stage_say in every stage

ID_SAY = "I am sorry, I cannot check your answers right now."
NEG_SAY = "I am sorry, I cannot save that arrangement right now."


def _farewell(node, v=None):
    v = dict(v or {"title": "Ms", "last_name": "Novak"})
    exec(_code(node), {"vars": v})
    return v["farewell"]


def _fail_spec(base, stages=("identity", "negotiate"), stage_say=None, say=None):
    s = copy.deepcopy(base)
    fe = next(e for e in s["events"] if e["id"] == s["confirmation"]["failure_event"])
    fe["stages"], fe["say"] = list(stages), say
    fe["stage_say"] = stage_say if stage_say is not None else {"identity": ID_SAY, "negotiate": NEG_SAY}
    return s


def test_i2_negotiate_failure_close_speaks_its_stage_sentence():
    from builder.compile import ID_FAIL_CLOSE
    s = _fail_spec(A)
    w = C(s, OA, base_url=URL)
    neg = w["nodes"]["negotiate"]
    fail = next(t["target_node"] for t in neg["transitions"] if t.get("description") == "Promise failed after the retries")
    said = _farewell(w["nodes"][fail])
    assert NEG_SAY in said and ID_SAY not in said
    assert fail == "close_service_error_negotiate"
    assert fail in json.dumps(w["nodes"]["retry_promise"])
    assert ID_SAY in _farewell(w["nodes"][ID_FAIL_CLOSE])
    assert _errors(w, s) == []


def test_i2_single_stage_event_uses_its_stage_sentence_in_the_default_close():
    s = _fail_spec(A, stages=("negotiate",), stage_say={"negotiate": NEG_SAY}, say="I could not record your promise.")
    w = C(s, OA, base_url=URL)
    said = _farewell(w["nodes"]["close_service_error"])
    assert NEG_SAY in said and "could not record" not in said
    assert not any(k.startswith("close_service_error_") for k in w["nodes"])


def test_i2_opening_and_third_party_sentences_get_their_own_closes():
    s = copy.deepcopy(A)
    ev = next(e for e in s["events"] if e["id"] == "deceased")
    ev["stage_say"] = {"opening": "My condolences.", "third_party": "I am very sorry to hear that."}
    w = C(s, OA, base_url=URL)
    n = w["nodes"]
    op = {t["target_node"] for t in n["opening"]["transitions"]}
    tp = {t["target_node"] for t in n["third_party"]["transitions"]}
    assert "close_deceased_opening" in op and "close_deceased_third_party" in tp
    assert "My condolences." in _farewell(n["close_deceased_opening"])
    assert "I am very sorry to hear that." in _farewell(n["close_deceased_third_party"])
    assert _errors(w, s) == []


def test_i2_insist_once_ask_uses_the_stage_sentence():
    s = copy.deepcopy(A)
    ev = next(e for e in s["events"] if e["id"] == "human")
    ev["stage_say"] = {"negotiate": "May I try to help with the payment first?"}
    n = C(s, OA, base_url=URL)["nodes"]
    assert n["human_hold_negotiate"]["on_enter"]["text"] == "May I try to help with the payment first?"
    assert n["human_hold_identity"]["on_enter"]["text"] == "Can I help you with that first?"


def test_i2_multi_wrap_fail_say_uses_negotiate_sentence():
    s = _fail_spec(B, stages=("negotiate",), stage_say={"negotiate": NEG_SAY}, say="I could not record your promise.")
    w = C(s, OA, base_url=URL)
    wraps = [k for k in w["nodes"] if k.startswith("wrap_")]
    assert wraps and all("FAIL_SAY = %r" % NEG_SAY in _code(w["nodes"][k]) for k in wraps)
    assert _errors(w, s) == []


def test_i2_cases_check_the_stage_sentence():
    from builder import cases
    s = _fail_spec(A)
    case = cases.build(s, OA, "service_error", "2026-10-14T11:00")
    assert 'the agent says "%s"' % NEG_SAY in case["checks"]
    assert NEG_SAY in cases.fixed_sentences(s) and ID_SAY in cases.fixed_sentences(s)
    s = copy.deepcopy(A)
    next(e for e in s["events"] if e["id"] == "deceased")["stage_say"] = {"third_party": "I am very sorry to hear that."}
    case = cases.build(s, OA, "event:deceased", "2026-10-14T11:00")
    assert 'the agent says "I am very sorry to hear that."' in case["checks"]


def test_i2_reference_specs_have_no_stage_variants():
    stages = ("_opening", "_identity", "_negotiate", "_third_party")
    for s in (A, B):
        n = C(s, OA, base_url=URL)["nodes"]
        assert not [k for k in n if k.startswith("close_") and k.endswith(stages)]

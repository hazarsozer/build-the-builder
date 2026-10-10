import copy
import json

from builder.compile import compile as C
from builder.lint import lint, opening_warnings

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))


def test_safe_recording_sentence_passes():
    assert opening_warnings(A["opening"]["first_sentence"]) == []


def test_banned_opening_words_warn():
    for s in ["Hello, this call is recorded.", "I am calling from collections.", "About your debt.",
              "About a payment.", "We collect data."]:
        assert opening_warnings(s), s


def test_lint_flags_code_braces():
    w = C(A, OA)
    code = w["nodes"]["setup"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]
    code["default"] += "\nx = {'a': {'b': 1}}\n"
    assert any("setup" in e and "{{" in e for e in lint(w, A))


def test_lint_flags_dead_end():
    w = C(A, OA)
    w["nodes"]["orphan"] = {"name": "orphan", "node_type": "conversation", "task_message": "x", "transitions": []}
    assert any("orphan" in e for e in lint(w, A))


def test_lint_flags_farewell_without_goodbye():
    w = C(A, OA)
    code = w["nodes"]["close_paid"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]
    code["default"] = code["default"].replace("Goodbye.", "See you.")
    assert any("close_paid" in e for e in lint(w, A))


def test_lint_flags_unknown_reason():
    s = copy.deepcopy(A)
    w = C(s, OA)
    code = w["nodes"]["close_dispute"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]
    code["default"] = code["default"].replace("'dispute'", "'made_up'")
    assert any("made_up" in e for e in lint(w, s))


def test_lint_flags_unreachable_node():
    w = C(A, OA)
    w["nodes"]["island"] = {"name": "island", "node_type": "code", "passthrough": True,
                            "passthrough_default_transition": "goodbye", "on_enter": {"type": "silent"},
                            "transitions": [{"target_node": "goodbye", "description": "always", "condition": {"operator": "always"}}]}
    errs = lint(w, A)
    assert any("island" in e and "unreachable" in e for e in errs)

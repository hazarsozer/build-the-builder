"""F2 (bench strict-wording, STW-01/06): a sentence the brief requires word for word in the opening, which extraction
put into an extra rule ("Right after the first sentence, say this second sentence word for word: "..."") instead of
into opening.first_sentence. The opening is spoken from fixed text, so such a rule never takes effect: the compiler
moves the sentence into the spoken opening (after the first sentence, in rule order, once) and drops the rule.
Fixture: the candidate-3 extraction of bench/strict-wording (results/bench/strict-wording)."""
import copy
import json

from builder.compile import compile as C, opening_verbatim_rules
from builder.lint import lint

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
SW = json.load(open("tests/fixtures/f2-strict-wording.spec.json"))
URL = "https://bank.example/v1"
PRIVACY = "Your personal data is processed under our privacy notice, which you can read on our website or at any branch."


def wf(spec):
    return C(spec, OA, base_url=URL)


def opening_text(w):
    return w["nodes"]["opening"]["on_enter"]["text"]


def everything(w):
    return json.dumps(w)


def test_strict_wording_opening_says_both_sentences():
    w = wf(SW)
    t = opening_text(w)
    assert PRIVACY in t
    assert t.count(PRIVACY) == 1
    # brief order: first sentence (identity + recording notice), privacy sentence, then the confirm question
    assert t.index("Halvard Mutual Bank") < t.index(PRIVACY) < t.index("Am I speaking with")
    assert "recorded" in SW["opening"]["first_sentence"]  # the brief describes the recording notice, gives no wording
    assert "This call is monitored and stored for quality and security purposes." in t


def test_strict_wording_dead_rule_dropped():
    w = wf(SW)
    s = everything(w)
    assert "Right after the first sentence" not in s
    # the other opening rule is kept
    assert "answer it first; the confirm question asked after that answer counts as the second ask" in s


def test_strict_wording_lint_clean():
    w = wf(SW)
    assert [e for e in lint(w, SW) if not e.startswith("warning")] == []


def test_rule_shapes_detected():
    assert [s for _, s in opening_verbatim_rules(SW)] == [PRIVACY]
    shapes = [
        ("opening", 'Right after the first sentence, say this second sentence word for word: "One two three four."'),
        ("any", 'At the start of the call, before anything else, say verbatim: "One two three four."'),
        ("opening", 'Say exactly "One two three four." as the second opening sentence.'),
        ("opening", "In the opening, say word for word: “One two three four.”"),
    ]
    for stage, text in shapes:
        sp = {"opening": {}, "extra_rules": [{"stage": stage, "text": text}]}
        assert [s for _, s in opening_verbatim_rules(sp)] == ["One two three four."], text
    not_shapes = [
        ("opening", 'If the person asks who is calling, say word for word: "One two three four."'),
        ("negotiate", 'At the start of negotiation, say word for word: "One two three four."'),
        ("opening", 'Right after the first sentence, mention the privacy notice.'),
        ("identity", 'After both questions pass, say this sentence word for word, before anything about the account: "One two three four."'),
    ]
    for stage, text in not_shapes:
        sp = {"opening": {}, "extra_rules": [{"stage": stage, "text": text}]}
        assert opening_verbatim_rules(sp) == [], text


def test_no_duplicate_when_already_in_opening():
    sp = copy.deepcopy(SW)
    sp["opening"]["first_sentence"] = sp["opening"]["first_sentence"] + " " + PRIVACY
    t = opening_text(wf(sp))
    assert t.count(PRIVACY) == 1


def test_two_rules_in_brief_order_once_each():
    sp = copy.deepcopy(SW)
    sp["extra_rules"].append({"stage": "opening", "text": 'As the third opening sentence, say word for word: "Calls may be reviewed by our compliance team."'})
    sp["extra_rules"].append({"stage": "any", "text": 'In the opening, say word for word: "Your personal data is processed under our privacy notice, which you can read on our website or at any branch."'})
    t = opening_text(wf(sp))
    assert t.count(PRIVACY) == 1
    assert t.index(PRIVACY) < t.index("Calls may be reviewed by our compliance team.") < t.index("Am I speaking with")


def test_moved_sentence_is_exempt_from_opening_word_checks_like_a_verbatim_first_sentence():
    sp = copy.deepcopy(SW)
    legal = "Calls are recorded and kept for five years under banking law."
    sp["extra_rules"].insert(1, {"stage": "opening", "text": 'Then say word for word as the third sentence: "%s"' % legal})
    w = wf(sp)
    assert legal in opening_text(w)
    assert [e for e in lint(w, sp) if not e.startswith("warning")] == []


def test_spec_without_such_rules_unchanged():
    for spec in (A, B):
        assert opening_verbatim_rules(spec) == []
        assert json.dumps(wf(spec), sort_keys=True) == json.dumps(wf(copy.deepcopy(spec)), sort_keys=True)
